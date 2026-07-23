from __future__ import annotations

from pathlib import Path

import fitz
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.databricks_genie_client import GenieResolution
from apps.api.db.models import Base, ExtractedQuestion, PdfPage, QuestionAnswer, SurveyPdfScan
from apps.api.openai_vision_client import ExtractedPageQuestion, VisionPageResult
from apps.api.pdf_scanner import WidgetMetadata
from apps.api.pdf_vision_service import PdfVisionService, _nearest_field_name, _search_bbox_normalized
from apps.api.settings import Settings


class _FakeVisionClient:
    configured = True

    def __init__(self, questions_by_page: dict[int, list[ExtractedPageQuestion]]) -> None:
        self._questions_by_page = questions_by_page

    def extract_page_questions(self, *, image_path: str, page_number: int) -> VisionPageResult:
        return VisionPageResult(
            questions=self._questions_by_page.get(page_number, []),
            request_payload={"page_number": page_number},
            response_payload={"ok": True},
            duration_seconds=0.01,
        )


class _FakeGenieClient:
    configured = True

    def resolve_many_candidates(self, *, candidates: list[dict], survey_year: int) -> dict[str, GenieResolution]:
        del survey_year
        return {
            candidate["candidate_id"]: GenieResolution(
                candidate_id=candidate["candidate_id"],
                sql_template="SELECT 42 AS value",
                table="catalog.schema.table",
                column="value",
                year_column="survey_year",
                value="42",
                confidence=90,
                reason="test resolution",
            )
            for candidate in candidates
        }


class _UnconfiguredGenieClient:
    configured = False

    def resolve_many_candidates(self, *, candidates: list[dict], survey_year: int) -> dict[str, GenieResolution]:
        raise AssertionError("should not be called when not configured")


def _build_fitz_pdf(path: Path, texts_by_page: dict[int, list[tuple[str, tuple[float, float]]]]) -> None:
    document = fitz.open()
    for page_number in sorted(texts_by_page):
        page = document.new_page(width=300, height=400)
        for text, (x, y) in texts_by_page[page_number]:
            page.insert_text((x, y), text, fontsize=10)
    document.save(str(path))
    document.close()


def _create_acroform_pdf(path: Path, field_names: list[str]) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    annotations = ArrayObject()
    fields = ArrayObject()
    for index, field_name in enumerate(field_names):
        bottom = 300 - (index * 40)
        annotation = DictionaryObject(
            {
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject(field_name),
                NameObject("/V"): TextStringObject(""),
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(
                    [FloatObject(20), FloatObject(bottom), FloatObject(220), FloatObject(bottom + 20)]
                ),
                NameObject("/F"): FloatObject(4),
                NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
            }
        )
        ref = writer._add_object(annotation)
        annotations.append(ref)
        fields.append(ref)
    page[NameObject("/Annots")] = annotations
    writer._root_object.update(
        {
            NameObject("/AcroForm"): DictionaryObject(
                {
                    NameObject("/Fields"): fields,
                    NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
                }
            )
        }
    )
    with path.open("wb") as handle:
        writer.write(handle)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


# ----------------------------------------------------------------------
# Bounding-box recovery
# ----------------------------------------------------------------------
def test_search_bbox_normalized_finds_text_on_page(tmp_path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _build_fitz_pdf(pdf_path, {1: [("Total enrollment", (30, 100))]})

    document = fitz.open(pdf_path)
    try:
        page = document[0]
        bbox = _search_bbox_normalized(page, "Total enrollment")
    finally:
        document.close()

    assert bbox is not None
    assert len(bbox) == 4
    assert all(0.0 <= value <= 1.0 for value in bbox)
    assert bbox[2] > bbox[0]
    assert bbox[3] > bbox[1]


def test_search_bbox_normalized_returns_none_when_not_found(tmp_path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _build_fitz_pdf(pdf_path, {1: [("Total enrollment", (30, 100))]})

    document = fitz.open(pdf_path)
    try:
        page = document[0]
        assert _search_bbox_normalized(page, "Nonexistent label") is None
        assert _search_bbox_normalized(page, "") is None
    finally:
        document.close()


# ----------------------------------------------------------------------
# Nearest AcroForm field matching
# ----------------------------------------------------------------------
def test_nearest_field_name_picks_closest_widget_within_cutoff() -> None:
    page_width, page_height = 300.0, 400.0
    widgets = [
        WidgetMetadata(field_name="FIELD_NEAR", page_number=1, rect=[20.0, 280.0, 220.0, 300.0], nearby_text=""),
        WidgetMetadata(field_name="FIELD_FAR", page_number=1, rect=[20.0, 20.0, 220.0, 40.0], nearby_text=""),
    ]
    # Normalized top-left-origin box near the top of the page, matching FIELD_NEAR's
    # flipped position (native PDF rect has bottom-left origin).
    bbox_near_top = [0.1, 0.24, 0.7, 0.28]

    assert _nearest_field_name(bbox_near_top, widgets, page_width, page_height) == "FIELD_NEAR"


def test_nearest_field_name_returns_empty_when_no_widget_is_close() -> None:
    widgets = [WidgetMetadata(field_name="FIELD_A", page_number=1, rect=[20.0, 20.0, 220.0, 40.0], nearby_text="")]
    far_bbox = [0.9, 0.9, 0.95, 0.95]
    assert _nearest_field_name(far_bbox, widgets, 300.0, 400.0) == ""
    assert _nearest_field_name([0.1, 0.1, 0.2, 0.2], [], 300.0, 400.0) == ""


# ----------------------------------------------------------------------
# Upload
# ----------------------------------------------------------------------
def test_upload_pdf_creates_scan_and_page_rows(tmp_path) -> None:
    source_pdf = tmp_path / "cds.pdf"
    _build_fitz_pdf(source_pdf, {1: [("Page one", (30, 100))], 2: [("Page two", (30, 100))]})

    session = _session_factory()()
    try:
        service = PdfVisionService(
            session,
            settings=Settings(pdf_upload_dir=str(tmp_path / "uploads")),
            vision_client=_FakeVisionClient({}),
            genie_client=_FakeGenieClient(),
        )
        scan = service.upload_pdf(file_bytes=source_pdf.read_bytes(), file_name="cds.pdf")

        assert scan.page_count == 2
        assert scan.status == "UPLOADED"

        pages = service.list_pages(scan.scan_id)
        assert [p.page_number for p in pages] == [1, 2]
        assert all(Path(p.image_path).exists() for p in pages)
    finally:
        session.close()


# ----------------------------------------------------------------------
# End-to-end processing
# ----------------------------------------------------------------------
def test_process_pdf_extracts_questions_and_resolves_via_genie(tmp_path) -> None:
    source_pdf = tmp_path / "cds.pdf"
    _build_fitz_pdf(source_pdf, {1: [("Total enrollment", (30, 100))]})

    fake_questions = {
        1: [
            ExtractedPageQuestion(
                display_id="P1_Q1",
                question="How many total students are enrolled?",
                answer_type="integer",
                confidence=0.9,
                source_text="Total enrollment",
            )
        ]
    }

    session = _session_factory()()
    try:
        service = PdfVisionService(
            session,
            settings=Settings(pdf_upload_dir=str(tmp_path / "uploads")),
            vision_client=_FakeVisionClient(fake_questions),
            genie_client=_FakeGenieClient(),
        )
        scan = service.upload_pdf(file_bytes=source_pdf.read_bytes(), file_name="cds.pdf")

        steps: list[str] = []
        ai_calls: list[dict] = []
        result = service.process_pdf(
            pdf_id=scan.scan_id,
            survey_year=2025,
            on_step=steps.append,
            on_ai_call=ai_calls.append,
        )

        assert result["questions_extracted"] == 1
        assert result["questions_answered"] == 1
        assert result["errors"] == []
        assert any("Completed page 1" in step for step in steps)
        providers = {call["provider"] for call in ai_calls}
        assert providers == {"openai_vision", "genie_api"}

        questions = service.list_questions(scan.scan_id)
        assert len(questions) == 1
        row = questions[0]
        assert row["question"] == "How many total students are enrolled?"
        assert row["answer"] == "42"
        assert row["sql"] == "SELECT 42 AS value"
        assert row["answer_confidence"] == 90
        assert row["status"] == "PENDING_REVIEW"
        # bbox should be recovered since "Total enrollment" is real text on the page
        assert row["bounding_box"] is not None
    finally:
        session.close()


def test_process_pdf_handles_unconfigured_genie_gracefully(tmp_path) -> None:
    source_pdf = tmp_path / "cds.pdf"
    _build_fitz_pdf(source_pdf, {1: [("Total enrollment", (30, 100))]})

    fake_questions = {
        1: [
            ExtractedPageQuestion(
                display_id="P1_Q1",
                question="How many total students are enrolled?",
                answer_type="integer",
                confidence=0.9,
                source_text="Total enrollment",
            )
        ]
    }

    session = _session_factory()()
    try:
        service = PdfVisionService(
            session,
            settings=Settings(pdf_upload_dir=str(tmp_path / "uploads")),
            vision_client=_FakeVisionClient(fake_questions),
            genie_client=_UnconfiguredGenieClient(),
        )
        scan = service.upload_pdf(file_bytes=source_pdf.read_bytes(), file_name="cds.pdf")
        service.process_pdf(pdf_id=scan.scan_id, survey_year=2025)

        questions = service.list_questions(scan.scan_id)
        assert questions[0]["answer"] == ""
        assert "not configured" in questions[0]["explanation"].lower()
    finally:
        session.close()


# ----------------------------------------------------------------------
# Review actions
# ----------------------------------------------------------------------
def test_review_actions_approve_edit_reject_and_rerun(tmp_path) -> None:
    source_pdf = tmp_path / "cds.pdf"
    _build_fitz_pdf(source_pdf, {1: [("Total enrollment", (30, 100))]})
    fake_questions = {
        1: [
            ExtractedPageQuestion(
                display_id="P1_Q1",
                question="How many total students are enrolled?",
                answer_type="integer",
                confidence=0.9,
                source_text="Total enrollment",
            )
        ]
    }

    session = _session_factory()()
    try:
        service = PdfVisionService(
            session,
            settings=Settings(pdf_upload_dir=str(tmp_path / "uploads")),
            vision_client=_FakeVisionClient(fake_questions),
            genie_client=_FakeGenieClient(),
        )
        scan = service.upload_pdf(file_bytes=source_pdf.read_bytes(), file_name="cds.pdf")
        service.process_pdf(pdf_id=scan.scan_id, survey_year=2025)
        question_id = service.list_questions(scan.scan_id)[0]["question_id"]

        approved = service.set_question_status(question_id, "APPROVED")
        assert approved.status == "APPROVED"

        edited = service.edit_answer(question_id, answer_text="1234")
        assert edited.answer == "1234"
        assert edited.status == "EDITED"

        rejected = service.set_question_status(question_id, "REJECTED")
        assert rejected.status == "REJECTED"

        rerun_answer = service.rerun_question(question_id, survey_year=2025)
        assert rerun_answer.answer == "42"
        assert rerun_answer.status == "PENDING_REVIEW"

        assert service.get_pdf_id_for_question(question_id) == scan.scan_id
        assert service.get_pdf_id_for_question("missing") is None
    finally:
        session.close()


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------
def test_export_approved_to_pdf_fills_matched_fields_only(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    output_pdf = tmp_path / "filled.pdf"
    _create_acroform_pdf(source_pdf, ["FIELD_A"])

    session = _session_factory()()
    try:
        scan = SurveyPdfScan(
            scan_id="pdfscan_export",
            survey_id="survey",
            file_name="source.pdf",
            file_path=str(source_pdf),
            file_sha256="abc",
            fillable=True,
            page_count=1,
            candidate_count=0,
            raw_result_json="{}",
            status="UPLOADED",
        )
        session.add(scan)
        page = PdfPage(
            id="pdfpage_1", pdf_id=scan.scan_id, page_number=1, image_path="", status="PROCESSED"
        )
        session.add(page)

        matched_question = ExtractedQuestion(
            id="pdfq_matched",
            page_id=page.id,
            display_id="P1_Q1",
            question="Field A value?",
            answer_type="integer",
            source_text="Field A",
            bounding_box_json="",
            matched_field_name="FIELD_A",
            confidence=0.9,
            status="EXTRACTED",
        )
        unmatched_question = ExtractedQuestion(
            id="pdfq_unmatched",
            page_id=page.id,
            display_id="P1_Q2",
            question="Some other value?",
            answer_type="integer",
            source_text="Other",
            bounding_box_json="",
            matched_field_name="",
            confidence=0.9,
            status="EXTRACTED",
        )
        session.add_all([matched_question, unmatched_question])
        session.add(
            QuestionAnswer(
                id="pdfqa_matched", question_id=matched_question.id, answer="777",
                sql_text="SELECT 777", explanation="ok", confidence=95, status="APPROVED",
            )
        )
        session.add(
            QuestionAnswer(
                id="pdfqa_unmatched", question_id=unmatched_question.id, answer="999",
                sql_text="SELECT 999", explanation="ok", confidence=95, status="APPROVED",
            )
        )
        session.commit()

        service = PdfVisionService(
            session,
            settings=Settings(pdf_export_dir=str(tmp_path / "exports")),
            vision_client=_FakeVisionClient({}),
            genie_client=_FakeGenieClient(),
        )
        result = service.export_approved_to_pdf(pdf_id=scan.scan_id, output_file_path=str(output_pdf))

        assert result["filled_count"] == 1
        assert result["skipped_no_field_match"] == ["pdfq_unmatched"]

        from pypdf import PdfReader

        fields = PdfReader(str(output_pdf)).get_fields()
        assert fields is not None
        assert fields["FIELD_A"].get("/V") == "777"
    finally:
        session.close()
