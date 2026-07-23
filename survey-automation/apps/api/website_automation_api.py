from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from apps.api.db.session import get_session
from apps.api.schemas import (
    WebsiteNextRequest,
    WebsiteNextResponse,
    WebsitePageResponse,
    WebsiteQuestionOverrideRequest,
    WebsiteQuestionResponse,
    WebsiteSessionResponse,
    WebsiteStartRequest,
    WebsiteStatusResponse,
)
from apps.api.settings import Settings, get_settings
from apps.api.website_automation_service import WebsiteAutomationService

router = APIRouter()


def _session_response(session_row) -> WebsiteSessionResponse:
    return WebsiteSessionResponse(
        session_id=session_row.id,
        url=session_row.url,
        status=session_row.status,
        current_page_number=session_row.current_page_number,
    )


@router.post("/website/start", response_model=WebsiteSessionResponse)
async def start_website_session(
    request: WebsiteStartRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebsiteSessionResponse:
    service = WebsiteAutomationService(session, settings=settings)
    session_row = service.start_session(url=request.url)
    return _session_response(session_row)


@router.post("/website/{session_id}/connect", response_model=WebsiteSessionResponse)
async def connect_website_browser(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebsiteSessionResponse:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        session_row = await service.connect_browser(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_response(session_row)


@router.post("/website/{session_id}/scan", response_model=list[WebsiteQuestionResponse])
async def scan_website_page(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[WebsiteQuestionResponse]:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        rows = await service.scan_page(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [WebsiteQuestionResponse(**row) for row in rows]


@router.post("/website/{session_id}/match", response_model=list[WebsiteQuestionResponse])
async def match_website_page(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[WebsiteQuestionResponse]:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        rows = service.match_page(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [WebsiteQuestionResponse(**row) for row in rows]


@router.post("/website/{session_id}/fill", response_model=list[WebsiteQuestionResponse])
async def fill_website_page(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[WebsiteQuestionResponse]:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        rows = await service.fill_page(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [WebsiteQuestionResponse(**row) for row in rows]


@router.post("/website/{session_id}/next", response_model=WebsiteNextResponse)
async def next_website_page(
    session_id: str,
    request: WebsiteNextRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebsiteNextResponse:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        result = await service.click_next(session_id, button_text=request.button_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WebsiteNextResponse(**result)


@router.get("/website/{session_id}/status", response_model=WebsiteStatusResponse)
async def get_website_session_status(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebsiteStatusResponse:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        status = service.get_status(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WebsiteStatusResponse(**status)


@router.get("/website/{session_id}/pages", response_model=list[WebsitePageResponse])
async def list_website_pages(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[WebsitePageResponse]:
    service = WebsiteAutomationService(session, settings=settings)
    pages = service.list_pages(session_id)
    return [
        WebsitePageResponse(
            page_id=page.id,
            page_number=page.page_number,
            page_url=page.page_url,
            screenshot_url=(
                f"/website-pages/{session_id}/{Path(page.screenshot_path).name}" if page.screenshot_path else ""
            ),
        )
        for page in pages
    ]


@router.get("/website-pages/{session_id}/{file_name}")
async def download_website_screenshot(
    session_id: str,
    file_name: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    safe_name = Path(file_name).name
    if safe_name != file_name:
        raise HTTPException(status_code=404, detail="Unknown screenshot")
    image_dir = (Path(settings.pdf_upload_dir) / "website_sessions" / session_id).resolve()
    image_path = (image_dir / safe_name).resolve()
    if image_path.parent != image_dir or not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown screenshot")
    return FileResponse(str(image_path), media_type="image/png", filename=safe_name)


@router.get("/website/{session_id}/questions", response_model=list[WebsiteQuestionResponse])
async def list_website_questions(
    session_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[WebsiteQuestionResponse]:
    service = WebsiteAutomationService(session, settings=settings)
    rows = service.list_questions(session_id)
    return [WebsiteQuestionResponse(**row) for row in rows]


@router.post("/website/question/{question_id}/override", response_model=WebsiteQuestionResponse)
async def override_website_question(
    question_id: str,
    request: WebsiteQuestionOverrideRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebsiteQuestionResponse:
    service = WebsiteAutomationService(session, settings=settings)
    try:
        service.override_question(question_id, status=request.status, answer_used=request.answer_used)
        row = service.get_question(question_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WebsiteQuestionResponse(**row)
