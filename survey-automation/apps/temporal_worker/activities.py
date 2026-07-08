from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from temporalio import activity

from apps.api.db.models import RunEvent, SkyvernTask
from apps.api.db.session import SessionLocal
from apps.api.service import Slice1Service
from apps.api.settings import get_settings
from apps.skyvern_worker.skyvern_client import SkyvernClient
from apps.temporal_worker.types import (
    ExecuteDraftFillPipelineInput,
    ExecuteDraftFillPipelineResult,
    ExecuteSectionPipelineInput,
    ExecuteSectionPipelineResult,
    ListStageWorkflowIdsInput,
    ListStageWorkflowIdsResult,
    RecordRunEventInput,
)


def _build_service(session: Session) -> Slice1Service:
    settings = get_settings()
    if not settings.skyvern_api_key:
        raise RuntimeError("SKYVERN_API_KEY is not configured")
    skyvern_client = SkyvernClient(base_url=settings.skyvern_base_url, api_key=settings.skyvern_api_key)
    callback_url = f"{settings.control_plane_public_base_url.rstrip('/')}/webhooks/skyvern"
    return Slice1Service(
        session=session,
        skyvern_client=skyvern_client,
        webhook_callback_url=callback_url,
        settings=settings,
    )


@activity.defn
def execute_section_pipeline_activity(request: ExecuteSectionPipelineInput) -> ExecuteSectionPipelineResult:
    with SessionLocal() as session:
        service = _build_service(session)
        result = service.execute_section_pipeline(
            run_id=request.run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
            browser_session_id=request.browser_session_id,
        )
        return ExecuteSectionPipelineResult(
            run_id=request.run_id,
            section_id=request.section_id,
            scan_task_id=str(result["scan_task_id"]),
            scan_workflow_id=str(result["scan_workflow_id"]),
            validate_task_ids=[str(task_id) for task_id in result["validate_task_ids"]],
            validate_workflow_ids=[str(workflow_id) for workflow_id in result["validate_workflow_ids"]],
            resolved_field_count=int(result["resolved_field_count"]),
        )


@activity.defn
def execute_draft_fill_pipeline_activity(request: ExecuteDraftFillPipelineInput) -> ExecuteDraftFillPipelineResult:
    with SessionLocal() as session:
        service = _build_service(session)
        result = service.execute_draft_fill_pipeline(
            run_id=request.run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
            browser_session_id=request.browser_session_id,
        )
        return ExecuteDraftFillPipelineResult(
            run_id=request.run_id,
            section_id=request.section_id,
            fill_task_ids=[str(task_id) for task_id in result["fill_task_ids"]],
            fill_workflow_ids=[str(workflow_id) for workflow_id in result["fill_workflow_ids"]],
            field_count=int(result["field_count"]),
            submit_enabled=bool(result["submit_enabled"]),
        )


@activity.defn
def list_stage_workflow_ids_activity(request: ListStageWorkflowIdsInput) -> ListStageWorkflowIdsResult:
    with SessionLocal() as session:
        tasks = (
            session.query(SkyvernTask)
            .filter(SkyvernTask.run_id == request.run_id)
            .filter(SkyvernTask.section_id == request.section_id)
            .filter(SkyvernTask.purpose == request.purpose)
            .filter(SkyvernTask.stage == request.stage)
            .order_by(SkyvernTask.chunk_index)
            .all()
        )
        return ListStageWorkflowIdsResult(
            task_ids=[task.task_id for task in tasks],
            workflow_ids=[task.workflow_id for task in tasks],
        )


@activity.defn
def record_run_event_activity(request: RecordRunEventInput) -> None:
    with SessionLocal() as session:
        session.add(
            RunEvent(
                run_id=request.run_id,
                event_type=request.event_type,
                payload_json=json.dumps(request.payload),
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
