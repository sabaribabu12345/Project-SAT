from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.models import WebsiteAction, WebsitePage, WebsiteQuestion, WebsiteSession
from apps.api.embedding_similarity import MappingSimilarityScorer, OpenAIEmbeddingSimilarityScorer
from apps.api.openai_vision_client import OpenAIVisionClient
from apps.api.pdf_vision_service import PdfVisionService
from apps.api.settings import Settings
from apps.api.website_browser_driver import (
    FormControlInfo,
    FormControlOption,
    PlaywrightBrowserDriver,
    WebsiteBrowserDriver,
)

_FILLABLE_STATUSES = {"APPROVED", "EDITED"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _SessionRuntime:
    driver: WebsiteBrowserDriver
    scorer: MappingSimilarityScorer


# In-process registry of live browser connections + per-session similarity
# scorers (constructing one scorer per session, not per request, avoids
# re-embedding the whole approved-answer pool on every page scan). Sessions
# do not survive a server restart — accepted limitation for this
# single-analyst interactive tool.
_session_runtimes: dict[str, _SessionRuntime] = {}


class WebsiteAutomationService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        vision_client: OpenAIVisionClient | None = None,
        driver_factory: type[WebsiteBrowserDriver] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings()
        self._vision_client = vision_client or OpenAIVisionClient(self._settings)
        self._driver_factory = driver_factory or PlaywrightBrowserDriver

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def start_session(self, *, url: str) -> WebsiteSession:
        session_row = WebsiteSession(
            id=f"websess_{uuid.uuid4().hex[:12]}",
            url=url,
            status="CREATED",
        )
        self._session.add(session_row)
        self._session.commit()
        return session_row

    async def connect_browser(self, session_id: str) -> WebsiteSession:
        session_row = self._get_session(session_id)

        driver = self._driver_factory(self._settings)
        await driver.connect()
        await driver.goto(session_row.url)

        scorer = OpenAIEmbeddingSimilarityScorer(self._settings)
        _session_runtimes[session_id] = _SessionRuntime(driver=driver, scorer=scorer)

        session_row.status = "BROWSER_CONNECTED"
        session_row.started_at = session_row.started_at or _utc_now()
        session_row.updated_at = _utc_now()
        self._session.commit()
        return session_row

    def _get_session(self, session_id: str) -> WebsiteSession:
        session_row = self._session.get(WebsiteSession, session_id)
        if not session_row:
            raise ValueError(f"Unknown session_id: {session_id}")
        return session_row

    def _get_runtime(self, session_id: str) -> _SessionRuntime:
        runtime = _session_runtimes.get(session_id)
        if runtime is None:
            raise RuntimeError(
                f"Session {session_id} has no connected browser (or the server restarted since "
                "connecting). Call /website/{session_id}/connect again."
            )
        return runtime

    # ------------------------------------------------------------------
    # Step: scan current page
    # ------------------------------------------------------------------
    async def scan_page(self, session_id: str) -> list[dict[str, Any]]:
        session_row = self._get_session(session_id)
        runtime = self._get_runtime(session_id)

        page_url = await runtime.driver.current_url()
        screenshot_bytes = await runtime.driver.screenshot()

        page_number = session_row.current_page_number
        screenshot_path = self._screenshot_path(session_id, page_number)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(screenshot_bytes)

        page_row = WebsitePage(
            id=f"webpage_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            page_number=page_number,
            screenshot_path=str(screenshot_path),
            page_url=page_url,
        )
        self._session.add(page_row)
        self._session.commit()

        vision_result = self._vision_client.extract_webpage_questions(
            image_bytes=screenshot_bytes, page_url=page_url
        )
        self._log_action(
            question_id="",
            action="scan",
            status="failed" if vision_result.error else "completed",
            detail={
                "page_id": page_row.id,
                "request": vision_result.request_payload,
                "response": vision_result.response_payload,
                "error": vision_result.error,
            },
        )
        if vision_result.error:
            raise RuntimeError(f"Vision scan failed: {vision_result.error}")

        question_rows: list[WebsiteQuestion] = []
        for extracted in vision_result.questions:
            row = WebsiteQuestion(
                id=f"webq_{uuid.uuid4().hex[:12]}",
                page_id=page_row.id,
                detected_question=extracted.question,
                question_type=extracted.question_type,
                possible_answers_json=json.dumps(extracted.possible_answers),
                confidence=int(round(extracted.confidence * 100)),
                status="DETECTED",
            )
            self._session.add(row)
            question_rows.append(row)
        self._session.commit()

        session_row.status = "SCANNING"
        session_row.updated_at = _utc_now()
        self._session.commit()

        return [self._question_to_dict(row, page_row) for row in question_rows]

    def _screenshot_path(self, session_id: str, page_number: int) -> Path:
        base = Path(self._settings.pdf_upload_dir) / "website_sessions" / session_id
        return base / f"page_{page_number}.png"

    # ------------------------------------------------------------------
    # Step: match detected questions against approved PDF answers (Stage A)
    # ------------------------------------------------------------------
    def match_page(self, session_id: str) -> list[dict[str, Any]]:
        session_row = self._get_session(session_id)
        runtime = self._get_runtime(session_id)

        page_row = self._current_page(session_id, session_row.current_page_number)
        if not page_row:
            raise ValueError(f"No scanned page found for session {session_id}; call /scan first")

        approved_pool = PdfVisionService(self._session, settings=self._settings).list_all_approved_answers()
        threshold = self._settings.website_match_confidence_threshold

        questions = list(
            self._session.execute(
                select(WebsiteQuestion)
                .where(WebsiteQuestion.page_id == page_row.id)
                .where(WebsiteQuestion.status == "DETECTED")
            ).scalars()
        )

        for question in questions:
            best_candidate = None
            best_score = -1
            for candidate in approved_pool:
                score = runtime.scorer.score_pair(question.detected_question, candidate["question"])
                if score is not None and score > best_score:
                    best_score = score
                    best_candidate = candidate

            if best_candidate is not None and best_score >= threshold:
                question.matched_answer_id = best_candidate["answer_id"]
                question.matched_question_text = best_candidate["question"]
                question.answer_used = best_candidate["answer"]
                question.confidence = best_score
                question.status = "MATCHED"
            else:
                question.confidence = max(best_score, 0)
                # left as DETECTED — no confident match; analyst can override manually
            question.updated_at = _utc_now()
            self._log_action(
                question_id=question.id,
                action="match",
                status="completed",
                detail={"best_score": best_score, "matched": question.status == "MATCHED"},
            )

        self._session.commit()
        session_row.status = "READY_FOR_REVIEW"
        session_row.updated_at = _utc_now()
        self._session.commit()

        return [self._question_to_dict(row, page_row) for row in questions]

    # ------------------------------------------------------------------
    # Step: fill approved answers into the live page (Stage B)
    # ------------------------------------------------------------------
    async def fill_page(self, session_id: str) -> list[dict[str, Any]]:
        session_row = self._get_session(session_id)
        runtime = self._get_runtime(session_id)

        page_row = self._current_page(session_id, session_row.current_page_number)
        if not page_row:
            raise ValueError(f"No scanned page found for session {session_id}; call /scan first")

        fillable = list(
            self._session.execute(
                select(WebsiteQuestion)
                .where(WebsiteQuestion.page_id == page_row.id)
                .where(WebsiteQuestion.status.in_(_FILLABLE_STATUSES))
            ).scalars()
        )
        if not fillable:
            return [self._question_to_dict(row, page_row) for row in self._all_page_questions(page_row.id)]

        controls = await runtime.driver.list_form_controls()

        session_row.status = "FILLING"
        session_row.updated_at = _utc_now()
        self._session.commit()

        for question in fillable:
            try:
                control = self._best_control_match(runtime.scorer, question.detected_question, controls)
                if control is None:
                    raise RuntimeError("No matching form control found on the page for this question")
                await self._fill_one_control(runtime.driver, control, question.answer_used)
                question.status = "FILLED"
                self._log_action(
                    question_id=question.id,
                    action="fill",
                    status="completed",
                    detail={"control_id": control.control_id or control.group_name, "value": question.answer_used},
                )
            except Exception as exc:  # noqa: BLE001
                question.status = "FAILED"
                self._log_action(
                    question_id=question.id,
                    action="fill",
                    status="failed",
                    detail={"error": str(exc)},
                )
            question.updated_at = _utc_now()
            self._session.commit()

        return [self._question_to_dict(row, page_row) for row in self._all_page_questions(page_row.id)]

    def _best_control_match(
        self,
        scorer: MappingSimilarityScorer,
        question_text: str,
        controls: list[FormControlInfo],
    ) -> FormControlInfo | None:
        best_control = None
        best_score = -1
        for control in controls:
            if not control.label_text:
                continue
            score = scorer.score_pair(question_text, control.label_text)
            if score is not None and score > best_score:
                best_score = score
                best_control = control
        return best_control

    async def _fill_one_control(self, driver: WebsiteBrowserDriver, control: FormControlInfo, value: str) -> None:
        if control.control_type in ("radio", "checkbox"):
            option = self._best_option_match(control, value)
            if option is None:
                raise RuntimeError(f"No matching option among {[o.label_text for o in control.options]!r}")
            await driver.check_option(option.control_id)
            return
        if control.control_type == "dropdown":
            option = self._best_option_match(control, value)
            if option is not None:
                await driver.select_dropdown_option(control.control_id, option.value)
            else:
                await driver.select_dropdown_option(control.control_id, value)
            return
        await driver.fill_text(control.control_id, value)

    def _best_option_match(self, control: FormControlInfo, value: str) -> FormControlOption | None:
        if not control.options:
            return None
        normalized_value = (value or "").strip().lower()
        for option in control.options:
            if option.label_text.strip().lower() == normalized_value or option.value.strip().lower() == normalized_value:
                return option
        # No exact match — fall back to a simple substring check before giving up.
        for option in control.options:
            if normalized_value and normalized_value in option.label_text.strip().lower():
                return option
        return None

    # ------------------------------------------------------------------
    # Step: next page
    # ------------------------------------------------------------------
    async def click_next(self, session_id: str, *, button_text: str | None = None) -> dict[str, Any]:
        session_row = self._get_session(session_id)
        runtime = self._get_runtime(session_id)

        clicked = await runtime.driver.click_next(button_text)
        self._log_action(
            question_id="",
            action="click_next",
            status="completed" if clicked else "not_found",
            detail={"button_text": button_text, "clicked": clicked},
        )
        if clicked:
            session_row.current_page_number += 1
            session_row.status = "BROWSER_CONNECTED"
        session_row.updated_at = _utc_now()
        self._session.commit()
        return {"clicked": clicked, "current_page_number": session_row.current_page_number}

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------
    def override_question(
        self,
        question_id: str,
        *,
        status: str | None = None,
        answer_used: str | None = None,
    ) -> WebsiteQuestion:
        question = self._session.get(WebsiteQuestion, question_id)
        if not question:
            raise ValueError(f"Unknown question_id: {question_id}")
        if answer_used is not None:
            question.answer_used = answer_used
        if status is not None:
            question.status = status
        question.updated_at = _utc_now()
        self._session.commit()
        self._log_action(
            question_id=question_id,
            action="override",
            status="completed",
            detail={"status": status, "answer_used": answer_used},
        )
        return question

    # ------------------------------------------------------------------
    # Dashboard reads
    # ------------------------------------------------------------------
    def list_pages(self, session_id: str) -> list[WebsitePage]:
        return list(
            self._session.execute(
                select(WebsitePage).where(WebsitePage.session_id == session_id).order_by(WebsitePage.page_number)
            ).scalars()
        )

    def get_question(self, question_id: str) -> dict[str, Any]:
        question = self._session.get(WebsiteQuestion, question_id)
        if not question:
            raise ValueError(f"Unknown question_id: {question_id}")
        page = self._session.get(WebsitePage, question.page_id)
        return self._question_to_dict(question, page)

    def get_status(self, session_id: str) -> dict[str, Any]:
        session_row = self._get_session(session_id)
        questions = self._all_session_questions(session_id)

        counts = {"detected": 0, "matched": 0, "filled": 0, "skipped": 0, "failed": 0}
        confidences: list[int] = []
        for question in questions:
            if question.status == "DETECTED":
                counts["detected"] += 1
            elif question.status in ("MATCHED", "APPROVED", "EDITED"):
                counts["matched"] += 1
            elif question.status == "FILLED":
                counts["filled"] += 1
            elif question.status == "SKIPPED":
                counts["skipped"] += 1
            elif question.status == "FAILED":
                counts["failed"] += 1
            if question.confidence:
                confidences.append(question.confidence)

        return {
            "session_id": session_row.id,
            "url": session_row.url,
            "status": session_row.status,
            "current_page_number": session_row.current_page_number,
            "questions_detected": len(questions),
            "questions_matched": counts["matched"],
            "questions_filled": counts["filled"],
            "questions_skipped": counts["skipped"],
            "questions_failed": counts["failed"],
            "average_confidence": round(sum(confidences) / len(confidences), 1) if confidences else 0,
        }

    def list_questions(self, session_id: str) -> list[dict[str, Any]]:
        pages = {
            page.id: page
            for page in self._session.execute(
                select(WebsitePage).where(WebsitePage.session_id == session_id).order_by(WebsitePage.page_number)
            ).scalars()
        }
        results = []
        for question in self._all_session_questions(session_id):
            page = pages.get(question.page_id)
            results.append(self._question_to_dict(question, page))
        results.sort(key=lambda row: (row["page_number"] or 0, row["question_id"]))
        return results

    def _all_session_questions(self, session_id: str) -> list[WebsiteQuestion]:
        page_ids = [
            page.id
            for page in self._session.execute(
                select(WebsitePage).where(WebsitePage.session_id == session_id)
            ).scalars()
        ]
        if not page_ids:
            return []
        return list(
            self._session.execute(
                select(WebsiteQuestion).where(WebsiteQuestion.page_id.in_(page_ids))
            ).scalars()
        )

    def _all_page_questions(self, page_id: str) -> list[WebsiteQuestion]:
        return list(
            self._session.execute(
                select(WebsiteQuestion).where(WebsiteQuestion.page_id == page_id)
            ).scalars()
        )

    def _current_page(self, session_id: str, page_number: int) -> WebsitePage | None:
        # A page can be scanned more than once (e.g. retried after a failed vision
        # call), leaving multiple WebsitePage rows for the same page_number — take
        # the most recent one, not just whichever SQLite happens to return first.
        return (
            self._session.execute(
                select(WebsitePage)
                .where(WebsitePage.session_id == session_id)
                .where(WebsitePage.page_number == page_number)
                .order_by(WebsitePage.created_at.desc())
            )
            .scalars()
            .first()
        )

    def _question_to_dict(self, question: WebsiteQuestion, page: WebsitePage | None) -> dict[str, Any]:
        return {
            "question_id": question.id,
            "page_number": page.page_number if page else None,
            "detected_question": question.detected_question,
            "question_type": question.question_type,
            "possible_answers": json.loads(question.possible_answers_json) if question.possible_answers_json else [],
            "matched_answer_id": question.matched_answer_id,
            "matched_question_text": question.matched_question_text,
            "confidence": question.confidence,
            "answer_used": question.answer_used,
            "status": question.status,
        }

    def _log_action(self, *, question_id: str, action: str, status: str, detail: dict[str, Any]) -> None:
        self._session.add(
            WebsiteAction(
                question_id=question_id,
                action=action,
                status=status,
                detail_json=json.dumps(detail, ensure_ascii=False, default=str),
            )
        )
        self._session.commit()
