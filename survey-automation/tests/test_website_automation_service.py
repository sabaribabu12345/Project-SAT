from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.db.models import (
    Base,
    ExtractedQuestion,
    PdfPage,
    QuestionAnswer,
    SurveyPdfScan,
    WebsitePage,
    WebsiteQuestion,
    WebsiteSession,
)
from apps.api.openai_vision_client import ExtractedWebpageQuestion, WebpageVisionResult
from apps.api.pdf_vision_service import PdfVisionService
from apps.api.settings import Settings
from apps.api.website_automation_service import WebsiteAutomationService, _SessionRuntime, _session_runtimes
from apps.api.website_browser_driver import FormControlInfo, FormControlOption


class _FakeScorer:
    """Deterministic stand-in for OpenAIEmbeddingSimilarityScorer.score_pair."""

    def __init__(self, overrides: dict[tuple[str, str], int | None] | None = None) -> None:
        self._overrides = overrides or {}

    def score_pair(self, left: str, right: str) -> int | None:
        if (left, right) in self._overrides:
            return self._overrides[(left, right)]
        left_l, right_l = left.strip().lower(), right.strip().lower()
        if left_l == right_l:
            return 100
        if left_l and right_l and (left_l in right_l or right_l in left_l):
            return 80
        return 10


class _FakeVisionClient:
    def __init__(self, questions: list[ExtractedWebpageQuestion], error: str = "") -> None:
        self._questions = questions
        self._error = error

    def extract_webpage_questions(self, *, image_bytes: bytes, page_url: str) -> WebpageVisionResult:
        return WebpageVisionResult(
            questions=[] if self._error else self._questions,
            request_payload={"page_url": page_url},
            response_payload={"ok": True},
            duration_seconds=0.01,
            error=self._error,
        )


class _FakeBrowserDriver:
    def __init__(self, controls: list[FormControlInfo] | None = None, click_next_result: bool = True) -> None:
        self.controls = controls or []
        self._click_next_result = click_next_result
        self.filled: list[tuple] = []

    async def current_url(self) -> str:
        return "https://survey.example.edu/page1"

    async def screenshot(self) -> bytes:
        return b"fake-png-bytes"

    async def list_form_controls(self) -> list[FormControlInfo]:
        return self.controls

    async def fill_text(self, control_id: str, value: str) -> None:
        self.filled.append(("fill_text", control_id, value))

    async def select_dropdown_option(self, control_id: str, option_value: str) -> None:
        self.filled.append(("select_dropdown_option", control_id, option_value))

    async def check_option(self, control_id: str) -> None:
        self.filled.append(("check_option", control_id))

    async def click_next(self, button_text: str | None = None) -> bool:
        return self._click_next_result

    async def close(self) -> None:
        pass


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed_approved_pdf_answer(
    session,
    *,
    scan_id: str,
    question: str,
    answer: str,
    status: str = "APPROVED",
) -> None:
    session.add(
        SurveyPdfScan(
            scan_id=scan_id,
            survey_id="survey",
            file_name="f.pdf",
            file_path="/tmp/f.pdf",
            file_sha256="x" * 64,
            fillable=True,
            page_count=1,
            candidate_count=0,
            raw_result_json="{}",
            status="UPLOADED",
        )
    )
    page = PdfPage(id=f"{scan_id}_page1", pdf_id=scan_id, page_number=1, image_path="", status="RENDERED")
    session.add(page)
    extracted = ExtractedQuestion(
        id=f"{scan_id}_q1",
        page_id=page.id,
        display_id="Q1",
        question=question,
        answer_type="integer",
        status="EXTRACTED",
    )
    session.add(extracted)
    session.add(
        QuestionAnswer(
            id=f"{scan_id}_qa1",
            question_id=extracted.id,
            answer=answer,
            sql_text="SELECT 1",
            explanation="ok",
            confidence=95,
            status=status,
        )
    )
    session.commit()


def _seed_website_session(session, *, url: str = "https://survey.example.edu") -> tuple[WebsiteSession, WebsitePage]:
    website_session = WebsiteSession(id="websess_test", url=url, status="BROWSER_CONNECTED", current_page_number=1)
    session.add(website_session)
    page = WebsitePage(id="webpage_test", session_id=website_session.id, page_number=1, screenshot_path="")
    session.add(page)
    session.commit()
    return website_session, page


def _register_runtime(session_id: str, driver: _FakeBrowserDriver, scorer: _FakeScorer) -> None:
    _session_runtimes[session_id] = _SessionRuntime(driver=driver, scorer=scorer)


def teardown_function(_fn) -> None:
    _session_runtimes.clear()


# ----------------------------------------------------------------------
# Cross-PDF approved-answer query
# ----------------------------------------------------------------------
def test_list_all_approved_answers_latest_wins_across_pdfs() -> None:
    session = _session_factory()()
    try:
        _seed_approved_pdf_answer(session, scan_id="scanA", question="How many undergrads?", answer="100")
        _seed_approved_pdf_answer(session, scan_id="scanB", question="What is the tuition?", answer="5000")

        # Superseding edit: a newer EDITED row should win over an older APPROVED row
        # for the SAME question — and since the latest status isn't APPROVED, it
        # must be excluded entirely (not resurrect the stale approved value).
        stale_question = session.get(ExtractedQuestion, "scanA_q1")
        assert stale_question is not None
        session.add(
            QuestionAnswer(
                id="scanA_qa2",
                question_id=stale_question.id,
                answer="999",
                sql_text="SELECT 2",
                explanation="edited",
                confidence=50,
                status="EDITED",
            )
        )
        session.commit()

        results = PdfVisionService(session).list_all_approved_answers()
        questions = {row["question"]: row["answer"] for row in results}

        assert "What is the tuition?" in questions
        assert questions["What is the tuition?"] == "5000"
        # scanA's only APPROVED row was superseded by a later EDITED row -> excluded
        assert "How many undergrads?" not in questions
    finally:
        session.close()


# ----------------------------------------------------------------------
# Stage A: website question <-> approved PDF question matching
# ----------------------------------------------------------------------
def test_match_page_matches_above_threshold_and_leaves_low_score_unmatched() -> None:
    session = _session_factory()()
    try:
        _seed_approved_pdf_answer(
            session, scan_id="scanA", question="Total Undergraduate Enrollment", answer="39435"
        )
        _, page = _seed_website_session(session)
        good_q = WebsiteQuestion(
            id="webq_good", page_id=page.id, detected_question="Total Undergraduate Enrollment", status="DETECTED"
        )
        bad_q = WebsiteQuestion(
            id="webq_bad", page_id=page.id, detected_question="Completely unrelated field", status="DETECTED"
        )
        session.add_all([good_q, bad_q])
        session.commit()

        _register_runtime("websess_test", _FakeBrowserDriver(), _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings(website_match_confidence_threshold=60))
        rows = service.match_page("websess_test")

        by_id = {row["question_id"]: row for row in rows}
        assert by_id["webq_good"]["status"] == "MATCHED"
        assert by_id["webq_good"]["answer_used"] == "39435"
        assert by_id["webq_good"]["confidence"] == 100

        assert by_id["webq_bad"]["status"] == "DETECTED"
    finally:
        session.close()


def test_match_page_uses_latest_scan_when_page_rescanned() -> None:
    """A page re-scanned after an earlier failed/empty scan leaves multiple
    WebsitePage rows for the same page_number — matching must operate on the
    most recent scan's questions, not a stale/empty earlier one."""
    session = _session_factory()()
    try:
        _seed_approved_pdf_answer(session, scan_id="scanA", question="Institution name", answer="CSULB")
        website_session = WebsiteSession(
            id="websess_test", url="https://x", status="BROWSER_CONNECTED", current_page_number=1
        )
        session.add(website_session)
        empty_page = WebsitePage(id="webpage_old", session_id=website_session.id, page_number=1, screenshot_path="")
        session.add(empty_page)
        session.commit()

        # Simulate time passing before the re-scan so created_at ordering is unambiguous.
        import time as _time

        _time.sleep(0.01)
        fresh_page = WebsitePage(id="webpage_new", session_id=website_session.id, page_number=1, screenshot_path="")
        session.add(fresh_page)
        question = WebsiteQuestion(
            id="webq_fresh", page_id=fresh_page.id, detected_question="Institution name", status="DETECTED"
        )
        session.add(question)
        session.commit()

        _register_runtime("websess_test", _FakeBrowserDriver(), _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings(website_match_confidence_threshold=60))
        rows = service.match_page("websess_test")

        assert len(rows) == 1
        assert rows[0]["question_id"] == "webq_fresh"
        assert rows[0]["status"] == "MATCHED"
    finally:
        session.close()


# ----------------------------------------------------------------------
# Fill: status gating + Stage B control/option resolution
# ----------------------------------------------------------------------
def test_fill_page_only_processes_approved_and_edited_questions() -> None:
    session = _session_factory()()
    try:
        _, page = _seed_website_session(session)
        approved = WebsiteQuestion(
            id="webq_approved", page_id=page.id, detected_question="Total Enrollment",
            answer_used="39435", status="APPROVED",
        )
        edited = WebsiteQuestion(
            id="webq_edited", page_id=page.id, detected_question="Institution Type",
            answer_used="Public", status="EDITED",
        )
        detected = WebsiteQuestion(
            id="webq_detected", page_id=page.id, detected_question="Untouched", status="DETECTED"
        )
        skipped = WebsiteQuestion(
            id="webq_skipped", page_id=page.id, detected_question="Skip me", status="SKIPPED"
        )
        session.add_all([approved, edited, detected, skipped])
        session.commit()

        controls = [
            FormControlInfo(control_id="#enroll", label_text="Total Enrollment", control_type="textbox"),
            FormControlInfo(control_id="#inst", label_text="Institution Type", control_type="dropdown",
                             options=[FormControlOption(control_id="", value="public", label_text="Public")]),
        ]
        driver = _FakeBrowserDriver(controls=controls)
        _register_runtime("websess_test", driver, _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings())
        rows = asyncio.run(service.fill_page("websess_test"))

        by_id = {row["question_id"]: row for row in rows}
        assert by_id["webq_approved"]["status"] == "FILLED"
        assert by_id["webq_edited"]["status"] == "FILLED"
        assert by_id["webq_detected"]["status"] == "DETECTED"
        assert by_id["webq_skipped"]["status"] == "SKIPPED"

        assert ("fill_text", "#enroll", "39435") in driver.filled
        assert ("select_dropdown_option", "#inst", "public") in driver.filled
    finally:
        session.close()


def test_fill_page_radio_group_selects_matching_option() -> None:
    session = _session_factory()()
    try:
        _, page = _seed_website_session(session)
        question = WebsiteQuestion(
            id="webq_radio", page_id=page.id, detected_question="Posted online?",
            answer_used="Yes", status="APPROVED",
        )
        session.add(question)
        session.commit()

        controls = [
            FormControlInfo(
                control_id="", label_text="Posted online?", control_type="radio", group_name="posted",
                options=[
                    FormControlOption(control_id="#posted_yes", value="yes", label_text="Yes"),
                    FormControlOption(control_id="#posted_no", value="no", label_text="No"),
                ],
            )
        ]
        driver = _FakeBrowserDriver(controls=controls)
        _register_runtime("websess_test", driver, _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings())
        asyncio.run(service.fill_page("websess_test"))

        assert ("check_option", "#posted_yes") in driver.filled
        assert ("check_option", "#posted_no") not in driver.filled
    finally:
        session.close()


def test_fill_page_marks_failed_when_no_control_matches() -> None:
    session = _session_factory()()
    try:
        _, page = _seed_website_session(session)
        question = WebsiteQuestion(
            id="webq_nomatch", page_id=page.id, detected_question="Nonexistent field",
            answer_used="value", status="APPROVED",
        )
        session.add(question)
        session.commit()

        driver = _FakeBrowserDriver(controls=[])
        _register_runtime("websess_test", driver, _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings())
        rows = asyncio.run(service.fill_page("websess_test"))

        assert rows[0]["status"] == "FAILED"
    finally:
        session.close()


# ----------------------------------------------------------------------
# Status lifecycle / overrides
# ----------------------------------------------------------------------
def test_override_question_approve_edit_and_skip() -> None:
    session = _session_factory()()
    try:
        _, page = _seed_website_session(session)
        question = WebsiteQuestion(
            id="webq_1", page_id=page.id, detected_question="Q", answer_used="1", status="MATCHED"
        )
        session.add(question)
        session.commit()

        service = WebsiteAutomationService(session, settings=Settings())
        updated = service.override_question("webq_1", status="APPROVED")
        assert updated.status == "APPROVED"

        updated = service.override_question("webq_1", status="EDITED", answer_used="2")
        assert updated.status == "EDITED"
        assert updated.answer_used == "2"

        updated = service.override_question("webq_1", status="SKIPPED")
        assert updated.status == "SKIPPED"
    finally:
        session.close()


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------
def test_scan_page_persists_page_and_questions() -> None:
    session = _session_factory()()
    try:
        website_session, _ = _seed_website_session(session)
        _register_runtime(website_session.id, _FakeBrowserDriver(), _FakeScorer())

        vision_client = _FakeVisionClient(
            [
                ExtractedWebpageQuestion(
                    question="Total Undergraduate Enrollment",
                    question_type="textbox",
                    possible_answers=[],
                    confidence=0.95,
                )
            ]
        )
        service = WebsiteAutomationService(
            session,
            settings=Settings(pdf_upload_dir=str(_tmp_upload_dir())),
            vision_client=vision_client,
        )
        rows = asyncio.run(service.scan_page(website_session.id))

        assert len(rows) == 1
        assert rows[0]["detected_question"] == "Total Undergraduate Enrollment"
        assert rows[0]["status"] == "DETECTED"

        status = service.get_status(website_session.id)
        assert status["questions_detected"] == 1
    finally:
        session.close()


def _tmp_upload_dir():
    import tempfile

    return tempfile.mkdtemp()


# ----------------------------------------------------------------------
# Next page
# ----------------------------------------------------------------------
def test_click_next_increments_page_only_on_success() -> None:
    session = _session_factory()()
    try:
        website_session, _ = _seed_website_session(session)

        _register_runtime(website_session.id, _FakeBrowserDriver(click_next_result=True), _FakeScorer())
        service = WebsiteAutomationService(session, settings=Settings())
        result = asyncio.run(service.click_next(website_session.id))
        assert result["clicked"] is True
        assert result["current_page_number"] == 2

        _register_runtime(website_session.id, _FakeBrowserDriver(click_next_result=False), _FakeScorer())
        result = asyncio.run(service.click_next(website_session.id))
        assert result["clicked"] is False
        assert result["current_page_number"] == 2  # unchanged
    finally:
        session.close()
