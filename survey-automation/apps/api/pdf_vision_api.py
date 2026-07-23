from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from apps.api.db.models import GenieApiCallHistory, SurveyPdfScan, WorkflowJob
from apps.api.db.session import SessionLocal, get_session
from apps.api.pdf_vision_service import PdfVisionService
from apps.api.schemas import (
    PdfVisionExportRequest,
    PdfVisionExportResponse,
    PdfVisionJobResponse,
    PdfVisionJobStatusResponse,
    PdfVisionPageResponse,
    PdfVisionProcessRequest,
    PdfVisionQuestionResponse,
    PdfVisionUploadResponse,
    QuestionEditRequest,
    QuestionRerunRequest,
)
from apps.api.settings import Settings, get_settings

router = APIRouter()


@router.post("/pdf/upload", response_model=PdfVisionUploadResponse)
async def upload_pdf(
    file: UploadFile,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionUploadResponse:
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    service = PdfVisionService(session, settings=settings)
    try:
        scan = service.upload_pdf(file_bytes=file_bytes, file_name=file.filename or "upload.pdf")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Unable to process PDF: {exc}") from exc

    return PdfVisionUploadResponse(
        pdf_id=scan.scan_id,
        file_name=scan.file_name,
        page_count=scan.page_count,
        status=scan.status,
        created_at=scan.created_at.isoformat(),
    )


@router.get("/pdf/{pdf_id}/pages", response_model=list[PdfVisionPageResponse])
def list_pdf_pages(
    pdf_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[PdfVisionPageResponse]:
    service = PdfVisionService(session, settings=settings)
    pages = service.list_pages(pdf_id)
    return [
        PdfVisionPageResponse(
            page_id=page.id,
            pdf_id=page.pdf_id,
            page_number=page.page_number,
            image_url=f"/pdf-pages/{pdf_id}/{Path(page.image_path).name}" if page.image_path else "",
            status=page.status,
        )
        for page in pages
    ]


@router.get("/pdf-pages/{pdf_id}/{file_name}")
def download_pdf_page_image(
    pdf_id: str,
    file_name: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    safe_name = Path(file_name).name
    if safe_name != file_name:
        raise HTTPException(status_code=404, detail="Unknown page image")
    image_dir = (Path(settings.pdf_upload_dir) / "pdf_page_contexts" / pdf_id / "images").resolve()
    image_path = (image_dir / safe_name).resolve()
    if image_path.parent != image_dir or not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown page image")
    return FileResponse(str(image_path), media_type="image/png", filename=safe_name)


@router.post("/pdf/{pdf_id}/process", response_model=PdfVisionJobResponse)
def process_pdf(
    pdf_id: str,
    request: PdfVisionProcessRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionJobResponse:
    if not session.get(SurveyPdfScan, pdf_id):
        raise HTTPException(status_code=404, detail=f"Unknown pdf_id: {pdf_id}")

    job_id = f"pdfvjob_{uuid.uuid4().hex[:12]}"
    request_payload = {"pdf_id": pdf_id, "job_type": "pdf_vision_process", "survey_year": request.survey_year}
    job = WorkflowJob(
        job_id=job_id,
        status="queued",
        request_json=json.dumps(request_payload),
        steps_json="[]",
    )
    session.add(job)
    session.commit()

    thread = threading.Thread(
        target=_execute_pdf_vision_job,
        kwargs={"job_id": job_id, "pdf_id": pdf_id, "survey_year": request.survey_year},
        daemon=True,
    )
    thread.start()
    return PdfVisionJobResponse(job_id=job_id, pdf_id=pdf_id, status="queued")


@router.get("/pdf/{pdf_id}/process/{job_id}", response_model=PdfVisionJobStatusResponse)
def get_pdf_vision_job(
    pdf_id: str,
    job_id: str,
    session: Session = Depends(get_session),
) -> PdfVisionJobStatusResponse:
    job = session.get(WorkflowJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    request_payload = json.loads(job.request_json or "{}")
    if request_payload.get("job_type") != "pdf_vision_process" or request_payload.get("pdf_id") != pdf_id:
        raise HTTPException(status_code=404, detail=f"Unknown job_id for pdf_id: {job_id}")
    return PdfVisionJobStatusResponse(
        job_id=job.job_id,
        pdf_id=pdf_id,
        status=job.status,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        steps=json.loads(job.steps_json or "[]"),
        result=json.loads(job.result_json) if job.result_json else None,
        error=job.error,
    )


@router.get("/pdf/{pdf_id}/questions", response_model=list[PdfVisionQuestionResponse])
def list_pdf_questions(
    pdf_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[PdfVisionQuestionResponse]:
    service = PdfVisionService(session, settings=settings)
    return [PdfVisionQuestionResponse(**row) for row in service.list_questions(pdf_id)]


@router.post("/question/{question_id}/approve", response_model=PdfVisionQuestionResponse)
def approve_question(
    question_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionQuestionResponse:
    service = PdfVisionService(session, settings=settings)
    return _apply_and_return(service, question_id, lambda: service.set_question_status(question_id, "APPROVED"))


@router.post("/question/{question_id}/reject", response_model=PdfVisionQuestionResponse)
def reject_question(
    question_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionQuestionResponse:
    service = PdfVisionService(session, settings=settings)
    return _apply_and_return(service, question_id, lambda: service.set_question_status(question_id, "REJECTED"))


@router.post("/question/{question_id}/edit", response_model=PdfVisionQuestionResponse)
def edit_question(
    question_id: str,
    request: QuestionEditRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionQuestionResponse:
    service = PdfVisionService(session, settings=settings)
    return _apply_and_return(service, question_id, lambda: service.edit_answer(question_id, answer_text=request.answer))


@router.post("/question/{question_id}/rerun", response_model=PdfVisionQuestionResponse)
def rerun_question(
    question_id: str,
    request: QuestionRerunRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionQuestionResponse:
    service = PdfVisionService(session, settings=settings)
    return _apply_and_return(
        service, question_id, lambda: service.rerun_question(question_id, survey_year=request.survey_year)
    )


@router.post("/pdf/{pdf_id}/export-filled-pdf", response_model=PdfVisionExportResponse)
def export_pdf_vision_filled_pdf(
    pdf_id: str,
    request: PdfVisionExportRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PdfVisionExportResponse:
    service = PdfVisionService(session, settings=settings)
    try:
        result = service.export_approved_to_pdf(
            pdf_id=pdf_id,
            output_file_path=request.output_file_path,
            flatten=request.flatten,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    output_path = Path(result["output_file_path"])
    export_dir = Path(settings.pdf_export_dir).resolve()
    download_url = ""
    if output_path.resolve().parent == export_dir:
        download_url = f"/pdf-exports/{output_path.name}"

    return PdfVisionExportResponse(
        pdf_id=pdf_id,
        source_file_path=result["source_file_path"],
        output_file_path=result["output_file_path"],
        download_url=download_url,
        filled_count=result["filled_count"],
        skipped_no_field_match=result["skipped_no_field_match"],
        missing_pdf_fields=result["missing_pdf_fields"],
    )


def _apply_and_return(
    service: PdfVisionService, question_id: str, action: Callable[[], Any]
) -> PdfVisionQuestionResponse:
    try:
        action()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    pdf_id = service.get_pdf_id_for_question(question_id)
    if not pdf_id:
        raise HTTPException(status_code=404, detail=f"Unknown question_id: {question_id}")
    for row in service.list_questions(pdf_id):
        if row["question_id"] == question_id:
            return PdfVisionQuestionResponse(**row)
    raise HTTPException(status_code=404, detail=f"Unknown question_id: {question_id}")


def _execute_pdf_vision_job(job_id: str, *, pdf_id: str, survey_year: int) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(WorkflowJob, job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = datetime.now(UTC)
            db.commit()

            service = PdfVisionService(db, settings=get_settings())
            step_rows: list[dict[str, Any]] = json.loads(job.steps_json or "[]")

            def _on_step(message: str) -> None:
                now = datetime.now(UTC).isoformat()
                step_rows.append(
                    {
                        "name": message,
                        "status": "completed",
                        "started_at": now,
                        "finished_at": now,
                    }
                )
                job.steps_json = json.dumps(step_rows)
                db.commit()

            def _on_ai_call(event: dict[str, Any]) -> None:
                row = GenieApiCallHistory(
                    job_id=job_id,
                    scan_id=pdf_id,
                    provider=str(event.get("provider") or "openai_vision"),
                    batch_index=int(event.get("batch_index") or 1),
                    status=str(event.get("status") or "completed"),
                    request_json=json.dumps(event.get("request_payload") or {}, ensure_ascii=False),
                    response_json=json.dumps(event.get("response_payload") or {}, ensure_ascii=False),
                    error=str(event.get("error") or "") or None,
                )
                db.add(row)
                db.commit()

            result = service.process_pdf(
                pdf_id=pdf_id,
                survey_year=survey_year,
                on_step=_on_step,
                on_ai_call=_on_ai_call,
            )
            job.status = "completed"
            job.finished_at = datetime.now(UTC)
            job.result_json = json.dumps(result)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as db:
            job = db.get(WorkflowJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = datetime.now(UTC)
                job.error = str(exc)
                db.commit()
