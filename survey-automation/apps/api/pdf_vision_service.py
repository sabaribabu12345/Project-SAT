from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import fitz
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.databricks_genie_client import DatabricksGenieClient
from apps.api.db.models import ExtractedQuestion, PdfPage, QuestionAnswer, SurveyPdfScan
from apps.api.openai_vision_client import OpenAIVisionClient
from apps.api.pdf_page_context import page_context_paths, render_pdf_page_to_png
from apps.api.pdf_scanner import WidgetMetadata, _scan_widget_metadata
from apps.api.service import fill_pdf_acroform_fields
from apps.api.settings import Settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PdfVisionService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        vision_client: OpenAIVisionClient | None = None,
        genie_client: DatabricksGenieClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings()
        self._vision_client = vision_client or OpenAIVisionClient(self._settings)
        self._genie_client = genie_client or DatabricksGenieClient(self._settings)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def upload_pdf(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        survey_id: str = "uploaded_pdf",
    ) -> SurveyPdfScan:
        upload_dir = Path(self._settings.pdf_upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        safe_name = Path(file_name).name or "upload.pdf"
        stored_path = upload_dir / f"{sha256[:16]}_{safe_name}"
        if not stored_path.exists():
            stored_path.write_bytes(file_bytes)

        with fitz.open(stored_path) as document:
            page_count = document.page_count

        scan_id = f"pdfscan_{uuid.uuid4().hex[:12]}"
        scan = SurveyPdfScan(
            scan_id=scan_id,
            survey_id=survey_id,
            file_name=safe_name,
            file_path=str(stored_path),
            file_sha256=sha256,
            fillable=False,
            page_count=page_count,
            candidate_count=0,
            raw_result_json="{}",
            status="UPLOADED",
        )
        self._session.add(scan)

        image_dir, _json_path = page_context_paths(
            scan_id=scan_id, page_number=1, base_dir=self._settings.pdf_upload_dir
        )
        now = _utc_now()
        for page_number in range(1, page_count + 1):
            image_path = render_pdf_page_to_png(
                pdf_path=str(stored_path),
                page_number=page_number,
                output_dir=str(image_dir),
            )
            page = PdfPage(
                id=f"pdfpage_{uuid.uuid4().hex[:12]}",
                pdf_id=scan_id,
                page_number=page_number,
                image_path=image_path,
                status="RENDERED",
                created_at=now,
                updated_at=now,
            )
            self._session.add(page)

        self._session.commit()
        return scan

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def process_pdf(
        self,
        *,
        pdf_id: str,
        survey_year: int,
        on_step: Callable[[str], None] | None = None,
        on_ai_call: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        scan = self._session.get(SurveyPdfScan, pdf_id)
        if not scan:
            raise ValueError(f"Unknown pdf_id: {pdf_id}")

        on_step = on_step or (lambda step: None)
        on_ai_call = on_ai_call or (lambda event: None)

        source_path = Path(scan.file_path)
        if not source_path.exists():
            raise ValueError(f"Source PDF not found: {scan.file_path}")

        acroform_rects = _acroform_rects_by_page(source_path)

        pages = list(
            self._session.execute(
                select(PdfPage).where(PdfPage.pdf_id == pdf_id).order_by(PdfPage.page_number)
            ).scalars()
        )

        questions_extracted = 0
        questions_answered = 0
        errors: list[str] = []

        document = fitz.open(source_path)
        try:
            for page_row in pages:
                page_number = page_row.page_number
                on_step(f"Processing page {page_number}")

                if not page_row.image_path:
                    image_dir, _json_path = page_context_paths(
                        scan_id=pdf_id, page_number=page_number, base_dir=self._settings.pdf_upload_dir
                    )
                    page_row.image_path = render_pdf_page_to_png(
                        pdf_path=str(source_path),
                        page_number=page_number,
                        output_dir=str(image_dir),
                    )

                vision_result = self._vision_client.extract_page_questions(
                    image_path=page_row.image_path,
                    page_number=page_number,
                )
                on_ai_call(
                    {
                        "provider": "openai_vision",
                        "batch_index": page_number,
                        "status": "failed" if vision_result.error else "completed",
                        "request_payload": vision_result.request_payload,
                        "response_payload": vision_result.response_payload,
                        "error": vision_result.error,
                    }
                )

                if vision_result.error:
                    errors.append(f"page {page_number} vision: {vision_result.error}")
                    page_row.status = "FAILED"
                    page_row.updated_at = _utc_now()
                    self._session.commit()
                    on_step(f"Failed page {page_number}")
                    continue

                pdf_page = document[page_number - 1]
                page_width, page_height = pdf_page.rect.width, pdf_page.rect.height
                page_widgets = acroform_rects.get(page_number, [])

                question_rows: list[ExtractedQuestion] = []
                for extracted in vision_result.questions:
                    bbox = _search_bbox_normalized(pdf_page, extracted.source_text)
                    matched_field_name = (
                        _nearest_field_name(bbox, page_widgets, page_width, page_height) if bbox else ""
                    )
                    row = ExtractedQuestion(
                        id=f"pdfq_{uuid.uuid4().hex[:12]}",
                        page_id=page_row.id,
                        display_id=extracted.display_id,
                        question=extracted.question,
                        answer_type=extracted.answer_type,
                        source_text=extracted.source_text,
                        bounding_box_json=json.dumps(bbox) if bbox else "",
                        matched_field_name=matched_field_name,
                        confidence=extracted.confidence,
                        status="EXTRACTED",
                    )
                    self._session.add(row)
                    question_rows.append(row)
                    questions_extracted += 1
                self._session.commit()

                if question_rows:
                    self._resolve_questions_via_genie(
                        question_rows,
                        survey_year=survey_year,
                        page_number=page_number,
                        on_ai_call=on_ai_call,
                    )
                    questions_answered += len(question_rows)

                page_row.status = "PROCESSED"
                page_row.updated_at = _utc_now()
                self._session.commit()
                on_step(f"Completed page {page_number}")
        finally:
            document.close()

        return {
            "pdf_id": pdf_id,
            "page_count": len(pages),
            "questions_extracted": questions_extracted,
            "questions_answered": questions_answered,
            "errors": errors,
        }

    def _resolve_questions_via_genie(
        self,
        question_rows: list[ExtractedQuestion],
        *,
        survey_year: int,
        page_number: int,
        on_ai_call: Callable[[dict[str, Any]], None],
    ) -> None:
        now = _utc_now()

        if not self._genie_client.configured:
            for row in question_rows:
                self._session.add(
                    QuestionAnswer(
                        id=f"pdfqa_{uuid.uuid4().hex[:12]}",
                        question_id=row.id,
                        answer="",
                        sql_text="",
                        explanation="Genie not configured: check DATABRICKS_HOST and DATABRICKS_GENIE_SPACE_ID",
                        confidence=0,
                        status="PENDING_REVIEW",
                        created_at=now,
                        updated_at=now,
                    )
                )
            self._session.commit()
            return

        genie_candidates = [
            {
                "candidate_id": row.id,
                "field_name": row.display_id or row.id,
                "section": f"CDS PDF page {page_number}",
                "label_text": row.question,
                "datapoint_intent": row.answer_type,
                "context": row.source_text,
            }
            for row in question_rows
        ]

        try:
            resolutions = self._genie_client.resolve_many_candidates(
                candidates=genie_candidates, survey_year=survey_year
            )
        except Exception as exc:  # noqa: BLE001
            on_ai_call(
                {
                    "provider": "genie_api",
                    "batch_index": page_number,
                    "status": "failed",
                    "request_payload": {"candidates": genie_candidates, "survey_year": survey_year},
                    "response_payload": {},
                    "error": str(exc),
                }
            )
            for row in question_rows:
                self._session.add(
                    QuestionAnswer(
                        id=f"pdfqa_{uuid.uuid4().hex[:12]}",
                        question_id=row.id,
                        answer="",
                        sql_text="",
                        explanation=f"Genie call failed: {exc}",
                        confidence=0,
                        status="PENDING_REVIEW",
                        created_at=now,
                        updated_at=now,
                    )
                )
            self._session.commit()
            return

        on_ai_call(
            {
                "provider": "genie_api",
                "batch_index": page_number,
                "status": "completed",
                "request_payload": {"candidates": genie_candidates, "survey_year": survey_year},
                "response_payload": {
                    "resolutions": {
                        candidate_id: {
                            "sql_template": resolution.sql_template,
                            "table": resolution.table,
                            "column": resolution.column,
                            "year_column": resolution.year_column,
                            "value": resolution.value,
                            "confidence": resolution.confidence,
                            "reason": resolution.reason,
                        }
                        for candidate_id, resolution in resolutions.items()
                    }
                },
                "error": "",
            }
        )

        for row in question_rows:
            result = resolutions.get(row.id)
            self._session.add(
                QuestionAnswer(
                    id=f"pdfqa_{uuid.uuid4().hex[:12]}",
                    question_id=row.id,
                    answer=(result.value if result else "") or "",
                    sql_text=(result.sql_template if result else "") or "",
                    explanation=(result.reason if result else "No Genie result returned for this question") or "",
                    confidence=int(result.confidence) if result else 0,
                    status="PENDING_REVIEW",
                    created_at=now,
                    updated_at=now,
                )
            )
        self._session.commit()

    # ------------------------------------------------------------------
    # Review dashboard reads
    # ------------------------------------------------------------------
    def list_pages(self, pdf_id: str) -> list[PdfPage]:
        return list(
            self._session.execute(
                select(PdfPage).where(PdfPage.pdf_id == pdf_id).order_by(PdfPage.page_number)
            ).scalars()
        )

    def list_questions(self, pdf_id: str) -> list[dict[str, Any]]:
        pages = {page.id: page for page in self.list_pages(pdf_id)}
        if not pages:
            return []

        questions = list(
            self._session.execute(
                select(ExtractedQuestion)
                .where(ExtractedQuestion.page_id.in_(pages.keys()))
                .order_by(ExtractedQuestion.id)
            ).scalars()
        )

        results: list[dict[str, Any]] = []
        for question in questions:
            page = pages.get(question.page_id)
            answer = self._get_latest_answer(question.id)
            results.append(
                {
                    "question_id": question.id,
                    "page_number": page.page_number if page else None,
                    "display_id": question.display_id,
                    "question": question.question,
                    "answer_type": question.answer_type,
                    "source_text": question.source_text,
                    "bounding_box": json.loads(question.bounding_box_json) if question.bounding_box_json else None,
                    "matched_field_name": question.matched_field_name,
                    "extraction_confidence": question.confidence,
                    "answer": answer.answer if answer else "",
                    "sql": answer.sql_text if answer else "",
                    "explanation": answer.explanation if answer else "",
                    "answer_confidence": answer.confidence if answer else 0,
                    "status": answer.status if answer else "PENDING_REVIEW",
                }
            )
        results.sort(key=lambda row: (row["page_number"] or 0, row["question_id"]))
        return results

    def list_all_approved_answers(self) -> list[dict[str, Any]]:
        """Cross-PDF pool of approved Q&A pairs, for matching against website questions.

        Dedupes to the latest QuestionAnswer per question_id (same rule as
        _get_latest_answer) before filtering to APPROVED, so a stale approved
        row superseded by a later edit/rerun is never resurrected.
        """
        all_answers = list(
            self._session.execute(
                select(QuestionAnswer).order_by(QuestionAnswer.question_id, QuestionAnswer.created_at.desc())
            ).scalars()
        )
        latest_by_question: dict[str, QuestionAnswer] = {}
        for answer in all_answers:
            if answer.question_id not in latest_by_question:
                latest_by_question[answer.question_id] = answer

        approved = [answer for answer in latest_by_question.values() if answer.status == "APPROVED"]
        if not approved:
            return []

        question_ids = [answer.question_id for answer in approved]
        questions = {
            question.id: question
            for question in self._session.execute(
                select(ExtractedQuestion).where(ExtractedQuestion.id.in_(question_ids))
            ).scalars()
        }

        page_ids = {question.page_id for question in questions.values()}
        pages = (
            {
                page.id: page
                for page in self._session.execute(select(PdfPage).where(PdfPage.id.in_(page_ids))).scalars()
            }
            if page_ids
            else {}
        )

        results: list[dict[str, Any]] = []
        for answer in approved:
            question = questions.get(answer.question_id)
            if not question:
                continue
            page = pages.get(question.page_id)
            results.append(
                {
                    "answer_id": answer.id,
                    "question_id": question.id,
                    "question": question.question,
                    "answer": answer.answer,
                    "pdf_id": page.pdf_id if page else "",
                    "page_number": page.page_number if page else None,
                }
            )
        return results

    def get_pdf_id_for_question(self, question_id: str) -> str | None:
        question = self._session.get(ExtractedQuestion, question_id)
        if not question:
            return None
        page = self._session.get(PdfPage, question.page_id)
        return page.pdf_id if page else None

    def _get_latest_answer(self, question_id: str) -> QuestionAnswer | None:
        return (
            self._session.execute(
                select(QuestionAnswer)
                .where(QuestionAnswer.question_id == question_id)
                .order_by(QuestionAnswer.created_at.desc())
            )
            .scalars()
            .first()
        )

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------
    def set_question_status(self, question_id: str, status: str) -> QuestionAnswer:
        answer = self._get_latest_answer(question_id)
        if not answer:
            raise ValueError(f"No answer found for question_id: {question_id}")
        answer.status = status
        answer.updated_at = _utc_now()
        self._session.commit()
        return answer

    def edit_answer(self, question_id: str, *, answer_text: str) -> QuestionAnswer:
        answer = self._get_latest_answer(question_id)
        if not answer:
            raise ValueError(f"No answer found for question_id: {question_id}")
        answer.answer = answer_text
        answer.status = "EDITED"
        answer.updated_at = _utc_now()
        self._session.commit()
        return answer

    def rerun_question(self, question_id: str, *, survey_year: int) -> QuestionAnswer:
        question = self._session.get(ExtractedQuestion, question_id)
        if not question:
            raise ValueError(f"Unknown question_id: {question_id}")
        page = self._session.get(PdfPage, question.page_id)
        page_number = page.page_number if page else 0

        self._resolve_questions_via_genie(
            [question],
            survey_year=survey_year,
            page_number=page_number,
            on_ai_call=lambda event: None,
        )
        answer = self._get_latest_answer(question_id)
        if not answer:
            raise ValueError("Rerun did not produce an answer")
        return answer

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_approved_to_pdf(
        self,
        *,
        pdf_id: str,
        output_file_path: str | None = None,
        flatten: bool = False,
    ) -> dict[str, Any]:
        scan = self._session.get(SurveyPdfScan, pdf_id)
        if not scan:
            raise ValueError(f"Unknown pdf_id: {pdf_id}")

        source_path = Path(scan.file_path)
        if not source_path.exists():
            raise ValueError(f"Source PDF not found: {scan.file_path}")

        values_by_field: dict[str, str] = {}
        skipped_no_field_match: list[str] = []
        for row in self.list_questions(pdf_id):
            if row["status"] != "APPROVED":
                continue
            field_name = row["matched_field_name"]
            if not field_name:
                skipped_no_field_match.append(row["question_id"])
                continue
            values_by_field[field_name] = row["answer"]

        if output_file_path:
            output_path = Path(output_file_path)
        else:
            export_dir = Path(self._settings.pdf_export_dir)
            output_path = export_dir / f"{pdf_id}_{source_path.stem}_vision_resolved.pdf"

        filled_count, missing_pdf_fields = fill_pdf_acroform_fields(
            source_path=source_path,
            values_by_field=values_by_field,
            output_path=output_path,
            flatten=flatten,
        )

        return {
            "pdf_id": pdf_id,
            "source_file_path": str(source_path),
            "output_file_path": str(output_path),
            "filled_count": filled_count,
            "skipped_no_field_match": skipped_no_field_match,
            "missing_pdf_fields": missing_pdf_fields,
        }


# ----------------------------------------------------------------------
# Bounding-box recovery + best-effort AcroForm field matching
# ----------------------------------------------------------------------
def _acroform_rects_by_page(source_path: Path) -> dict[int, list[WidgetMetadata]]:
    try:
        reader = PdfReader(str(source_path))
    except Exception:  # noqa: BLE001
        return {}

    by_page: dict[int, list[WidgetMetadata]] = {}
    for widget in _scan_widget_metadata(reader).values():
        if widget.rect:
            by_page.setdefault(widget.page_number, []).append(widget)
    return by_page


def _search_bbox_normalized(page: fitz.Page, source_text: str) -> list[float] | None:
    """Locate source_text on the page and return a [x0,y0,x1,y1] box normalized to
    0..1 fractions of the page, in the same top-left-origin space as the rendered
    PNG. Returns None if the snippet can't be found (no highlight, not an error)."""
    text = (source_text or "").strip()
    if not text:
        return None
    try:
        rects = page.search_for(text)
    except Exception:  # noqa: BLE001
        return None
    if not rects:
        return None

    rect = rects[0]
    width, height = page.rect.width, page.rect.height
    if not width or not height:
        return None
    return [rect.x0 / width, rect.y0 / height, rect.x1 / width, rect.y1 / height]


def _nearest_field_name(
    bbox_normalized: list[float],
    widgets: list[WidgetMetadata],
    page_width: float,
    page_height: float,
) -> str:
    """Best-effort suggestion only — used to pre-fill matched_field_name for review,
    never treated as authoritative. Analysts can override it before export."""
    if not widgets or not page_width or not page_height:
        return ""

    bx = (bbox_normalized[0] + bbox_normalized[2]) / 2
    by = (bbox_normalized[1] + bbox_normalized[3]) / 2

    best_name = ""
    best_distance: float | None = None
    for widget in widgets:
        rect = widget.rect
        if not rect or len(rect) != 4:
            continue
        left, bottom, right, top = rect
        # AcroForm /Rect is bottom-left-origin (y increases upward); flip to the
        # same top-left-origin fractional space used for the PyMuPDF search box.
        wx = ((left + right) / 2) / page_width
        wy = (page_height - (bottom + top) / 2) / page_height
        distance = ((bx - wx) ** 2 + (by - wy) ** 2) ** 0.5
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_name = widget.field_name

    max_normalized_distance = 0.06
    if best_distance is not None and best_distance <= max_normalized_distance:
        return best_name
    return ""
