from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from apps.api.db.engine import get_engine
from apps.api.db.models import AnalystSqlQuery, Base, GenieApiCallHistory, Run, SurveyPdfDataPointCandidate, WorkflowJob
from apps.api.db.session import SessionLocal, get_session
from apps.api.schemas import (
    AnalystSqlAutoMapRequest,
    AnalystSqlAutoMapResponse,
    AnalystSqlMappingDraftResponse,
    AnalystSqlPreviewRequest,
    AnalystSqlPreviewResponse,
    AnalystSqlRerunRequest,
    AnalystSqlRerunResponse,
    AutoMapPdfScanRequest,
    AutoMapPdfScanResponse,
    ApproveAnalystSqlMappingDraftRequest,
    ApproveAnalystSqlMappingDraftResponse,
    ApprovePdfMappingDraftRequest,
    ApprovePdfMappingDraftResponse,
    ApproveFieldDiscoveryRequest,
    BootstrapFakeFormMasterDataPointsRequest,
    BootstrapMasterDataPointsRequest,
    BootstrapMasterDataPointsResponse,
    CatalogFieldResponse,
    CreateMasterDataPointRequest,
    CreateMasterDataPointAliasRequest,
    CreateRunRequest,
    DispatchFillRequest,
    DispatchFillResponse,
    DispatchScanFieldsRequest,
    DispatchScanFieldsResponse,
    DispatchValidateRequest,
    DispatchValidateResponse,
    ExecuteDraftFillPipelineRequest,
    ExecuteDraftFillPipelineResponse,
    ExecuteSectionPipelineRequest,
    ExecuteSectionPipelineResponse,
    FieldDiscoveryDraftResponse,
    FilledPdfExportRequest,
    FilledPdfExportResponse,
    GenieDraftMappingItemResponse,
    GenieApiCallHistoryResponse,
    GenieDraftMappingsJobResponse,
    GenieDraftMappingsJobStatusResponse,
    GenieDraftMappingsRequest,
    GenieDraftMappingsResponse,
    GenieResolveRequest,
    GenieResolutionResult,
    DirectResolveRequest,
    DirectResolutionResult,
    ResolvedValueResponse,
    MapPdfCandidateRequest,
    CandidateMappingSuggestionsResponse,
    CandidateSectionGroup,
    CandidatesBySectionResponse,
    MasterDataPointResponse,
    MasterDataPointAliasResponse,
    MappingSuggestionResponse,
    PdfCandidateResponse,
    PdfScanRequest,
    PdfScanResponse,
    PrepareSectionPayloadRequest,
    PrepareSectionPayloadResponse,
    PublishPdfScanCatalogRequest,
    RejectFieldDiscoveryRequest,
    ResolvePdfScanRequest,
    ResolvePdfScanResponse,
    ReviewItemResponse,
    RunMetricsResponse,
    RunEventResponse,
    SkyvernWebhookResponse,
    StartWorkflowRequest,
    StartWorkflowResponse,
    UpdateCatalogBindingRequest,
    UpdateMasterDataPointBindingRequest,
)
from apps.api.analyst_sql_mapping import AnalystSqlMappingService, DatabricksServingSqlMapper, cds_section_for
from apps.api.databricks_resolver import DatabricksSqlValueReader
from apps.api.fill_preview import build_fill_preview
from apps.api.operator_pages import data_points_page_html, fill_preview_js
from apps.api.pdf_vision_api import router as pdf_vision_router
from apps.api.pdf_vision_page import pdf_vision_ops_page_html
from apps.api.schemas import FillPreviewResponse
from apps.api.service import PdfDatapointService, PdfLabelEnrichmentFailedError, Slice1Service
from apps.api.settings import Settings, get_settings
from apps.api.website_automation_api import router as website_automation_router
from apps.api.website_automation_page import website_automation_page_html
from apps.skyvern_worker.skyvern_client import SkyvernClient
from apps.temporal_worker.signaler import TemporalSignaler

app = FastAPI(title="Survey Automation Control Plane", version="0.1.0")
app.include_router(pdf_vision_router)
app.include_router(website_automation_router)


class FullWorkflowLaunchRequest(BaseModel):
    portal_url: str = "http://localhost:8088/?realData=1"
    timeout_seconds: int = Field(default=1800, ge=120, le=7200)
    survey_year: int | None = None
    validate: bool = True
    browser_session_id: str | None = None
    use_current_browser: bool = False
    needs_login: bool = False
    username: str | None = None
    password: str | None = None
    skyvern_max_steps: int = Field(default=80, ge=20, le=300)


def _workflow_request_for_storage(payload: FullWorkflowLaunchRequest) -> dict[str, Any]:
    stored = payload.model_dump()
    stored.pop("username", None)
    stored.pop("password", None)
    return stored


def _effective_browser_session_id(payload: FullWorkflowLaunchRequest) -> str | None:
    session_id = (payload.browser_session_id or "").strip()
    if session_id:
        return session_id
    if payload.use_current_browser:
        return f"sess_{uuid.uuid4().hex[:12]}"
    return None


def _browser_runtime_config() -> dict[str, str | None]:
    settings = get_settings()
    browser_type = (os.getenv("BROWSER_TYPE") or settings.browser_type or "chromium-headful").strip()
    cdp_url = (os.getenv("BROWSER_REMOTE_DEBUGGING_URL") or settings.browser_remote_debugging_url or "").strip()
    return {
        "browser_type": browser_type,
        "cdp_url": cdp_url or None,
    }


def _app_root() -> Path:
    # /app/apps/api/main.py -> /app
    return Path(__file__).resolve().parents[2]


def _scripts_dir() -> Path:
    env_path = os.getenv("WORKFLOW_SCRIPTS_DIR")
    candidates = [
        Path(env_path) if env_path else None,
        _app_root() / "infra" / "scripts",
        # local checkout fallback
        Path(__file__).resolve().parents[2] / "infra" / "scripts",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise RuntimeError("Unable to locate infra/scripts directory for workflow automation")


def _fake_form_data_path() -> Path:
    env_path = os.getenv("FAKE_FORM_DATA_PATH")
    candidates = [
        Path(env_path) if env_path else None,
        Path("/app/fake-survey-form/fake-survey-form-data.json"),
        Path(__file__).resolve().parents[3] / "fake-survey-form" / "fake-survey-form-data.json",
    ]
    for candidate in candidates:
        if candidate and candidate.parent.exists():
            return candidate
    raise RuntimeError("Unable to locate fake-survey-form-data.json path")


def _fake_form_input_data_path(output_path: Path) -> Path:
    if output_path.exists():
        return output_path
    example_path = output_path.with_name("fake-survey-form-data.example.json")
    if example_path.exists():
        return example_path
    return output_path


def _extract_json_blob(output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"raw_output": output.strip()}
    payload = output[start : end + 1]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw_output": output.strip()}
    if isinstance(parsed, dict):
        return parsed
    return {"raw_output": output.strip()}


def _json_payload(raw: str | None, *, default: object) -> object:
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return parsed


def _run_command_step(
    *,
    job_id: str,
    name: str,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    finished_at = datetime.now(UTC).isoformat()
    output = completed.stdout or ""
    error = completed.stderr or ""
    parsed_output = _extract_json_blob(output)

    step = {
        "name": name,
        "status": "completed" if completed.returncode == 0 else "failed",
        "started_at": started_at,
        "finished_at": finished_at,
        "command": cmd,
        "return_code": completed.returncode,
        "stdout": output[-8000:],
        "stderr": error[-4000:],
        "parsed_output": parsed_output,
    }
    _job_append_step(job_id, step)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Step '{name}' failed with return code {completed.returncode}. "
            f"stderr={error[-800:] or '(empty)'}"
        )
    return step


def _job_append_step(job_id: str, step: dict[str, Any]) -> None:
    with SessionLocal() as db:
        job = db.get(WorkflowJob, job_id)
        if job:
            steps = json.loads(job.steps_json)
            steps.append(step)
            job.steps_json = json.dumps(steps)
            db.commit()


def _job_update(job_id: str, **fields: Any) -> None:
    with SessionLocal() as db:
        job = db.get(WorkflowJob, job_id)
        if not job:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()


def _execute_full_workflow_job(job_id: str, payload: FullWorkflowLaunchRequest) -> None:
    _job_update(job_id, status="running", started_at=datetime.now(UTC))

    try:
        script_dir = _scripts_dir()
        app_root = _app_root()
        data_path = _fake_form_data_path()
        env = os.environ.copy()

        pull_cmd = [
            "python",
            str(script_dir / "pull_real_fake_form_data.py"),
            "--input",
            str(_fake_form_input_data_path(data_path)),
            "--output",
            str(data_path),
            "--static-output",
            str(data_path),
        ]
        if payload.validate:
            pull_cmd.append("--validate")
        if payload.survey_year is not None:
            pull_cmd.extend(["--year", str(payload.survey_year)])

        pull_step = _run_command_step(
            job_id=job_id,
            name="pull_real_data",
            cmd=pull_cmd,
            cwd=app_root,
            env=env,
        )

        run_cmd = [
            "python",
            str(script_dir / "run_website_form_fill.py"),
            "--url",
            payload.portal_url,
            "--data",
            str(data_path),
            "--max-steps",
            str(payload.skyvern_max_steps),
            "--timeout-seconds",
            str(payload.timeout_seconds),
        ]
        browser_session_id = _effective_browser_session_id(payload)
        if browser_session_id:
            run_cmd.extend(["--browser-session-id", browser_session_id])
        if payload.needs_login:
            username = (payload.username or "").strip()
            password = payload.password or ""
            if not username or not password:
                raise RuntimeError("username and password are required when needs_login is true")
            env["WEBSITE_LOGIN_USERNAME"] = username
            env["WEBSITE_LOGIN_PASSWORD"] = password
        run_step = _run_command_step(
            job_id=job_id,
            name="run_full_fill",
            cmd=run_cmd,
            cwd=app_root,
            env=env,
        )

        _job_update(
            job_id,
            status="completed",
            finished_at=datetime.now(UTC),
            result_json=json.dumps({
                "pull_real_data": pull_step["parsed_output"],
                "run_full_fill": run_step["parsed_output"],
            }),
        )
    except Exception as exc:  # noqa: BLE001
        _job_update(job_id, status="failed", finished_at=datetime.now(UTC), error=str(exc))


def _execute_genie_draft_mappings_job(
    job_id: str,
    *,
    scan_id: str,
    payload: GenieDraftMappingsRequest,
) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(WorkflowJob, job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = datetime.now(UTC)
            db.commit()

            service = PdfDatapointService(db, settings=get_settings())
            step_rows = json.loads(job.steps_json or "[]")

            def _on_progress(event: dict[str, Any]) -> None:
                started_at = datetime.now(UTC).isoformat()
                finished_at = started_at
                completed = int(event.get("completed_batches") or 0)
                total = int(event.get("total_batches") or 0)
                step_rows.append(
                    {
                        "name": f"genie_batch_{completed}_of_{total}",
                        "status": "completed",
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "command": [],
                        "return_code": 0,
                        "stdout": "",
                        "stderr": "",
                        "parsed_output": event,
                    }
                )
                job.steps_json = json.dumps(step_rows)
                db.commit()

            def _on_genie_call(event: dict[str, Any]) -> None:
                row = GenieApiCallHistory(
                    job_id=job_id,
                    scan_id=scan_id,
                    provider=str(event.get("provider") or "genie_api"),
                    batch_index=int(event.get("batch_index") or 1),
                    status=str(event.get("status") or "completed"),
                    request_json=json.dumps(event.get("request_payload") or {}, ensure_ascii=False),
                    response_json=json.dumps(
                        {
                            "response_payload": event.get("response_payload"),
                            "client_trace": event.get("client_trace"),
                        },
                        ensure_ascii=False,
                    ),
                    error=str(event.get("error") or "") or None,
                )
                db.add(row)
                db.commit()

            result = service.generate_pdf_mapping_drafts(
                scan_id=scan_id,
                min_score=payload.min_score,
                include_already_mapped=payload.include_already_mapped,
                limit_candidates=payload.limit_candidates,
                provider=payload.provider,
                overwrite_existing=payload.overwrite_existing,
                genie_batch_size=payload.genie_batch_size,
                progress_callback=_on_progress,
                genie_call_callback=_on_genie_call,
            )
            job.status = "completed"
            job.finished_at = datetime.now(UTC)
            job.result_json = json.dumps(
                {
                    "scan_id": result.scan_id,
                    "total_candidates": result.total_candidates,
                    "drafted_count": result.drafted_count,
                    "skipped_count": result.skipped_count,
                    "skipped_already_mapped": result.skipped_already_mapped,
                    "skipped_low_score": result.skipped_low_score,
                }
            )
            db.commit()
    except Exception as exc:  # noqa: BLE001
        _job_update(job_id, status="failed", finished_at=datetime.now(UTC), error=str(exc))


@app.on_event("startup")
def on_startup() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema_updates()


def _ensure_sqlite_schema_updates() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info('skyvern_tasks')"))
        existing_columns = {str(row[1]) for row in result.fetchall()}
        if "stage" not in existing_columns:
            conn.execute(text("ALTER TABLE skyvern_tasks ADD COLUMN stage TEXT NOT NULL DEFAULT 'dispatch_validate'"))
        if "chunk_index" not in existing_columns:
            conn.execute(text("ALTER TABLE skyvern_tasks ADD COLUMN chunk_index INTEGER NOT NULL DEFAULT 0"))
        if "chunk_total" not in existing_columns:
            conn.execute(text("ALTER TABLE skyvern_tasks ADD COLUMN chunk_total INTEGER NOT NULL DEFAULT 1"))
        result = conn.execute(text("PRAGMA table_info('survey_pdf_datapoint_candidates')"))
        pdf_candidate_columns = {str(row[1]) for row in result.fetchall()}
        if pdf_candidate_columns:
            if "label_source" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN label_source TEXT NOT NULL DEFAULT ''"))
            if "field_rect_json" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN field_rect_json TEXT NOT NULL DEFAULT '[]'"))
            if "nearby_text" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN nearby_text TEXT NOT NULL DEFAULT ''"))
            if "datapoint_intent" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN datapoint_intent TEXT NOT NULL DEFAULT ''"))
            if "genie_sql_template" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_sql_template TEXT NOT NULL DEFAULT ''"))
            if "genie_table" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_table TEXT NOT NULL DEFAULT ''"))
            if "genie_column" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_column TEXT NOT NULL DEFAULT ''"))
            if "genie_year_column" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_year_column TEXT NOT NULL DEFAULT ''"))
            if "genie_value" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_value TEXT NOT NULL DEFAULT ''"))
            if "genie_confidence" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_confidence INTEGER NOT NULL DEFAULT 0"))
            if "genie_reason" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_reason TEXT NOT NULL DEFAULT ''"))
            if "genie_resolved_at" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN genie_resolved_at DATETIME"))
            if "direct_sql_failures" not in pdf_candidate_columns:
                conn.execute(text("ALTER TABLE survey_pdf_datapoint_candidates ADD COLUMN direct_sql_failures INTEGER NOT NULL DEFAULT 0"))
        conn.commit()


def _build_slice1_service(session: Session) -> Slice1Service:
    settings = get_settings()
    api_key = settings.skyvern_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="SKYVERN_API_KEY is not configured")
    skyvern_client = SkyvernClient(base_url=settings.skyvern_base_url, api_key=api_key)
    callback_url = f"{settings.control_plane_public_base_url.rstrip('/')}/webhooks/skyvern"
    return Slice1Service(
        session=session,
        skyvern_client=skyvern_client,
        webhook_callback_url=callback_url,
        settings=settings,
    )


def _pdf_candidate_response(candidate: Any) -> PdfCandidateResponse:
    try:
        field_rect = json.loads(candidate.field_rect_json or "[]")
    except json.JSONDecodeError:
        field_rect = []
    if not isinstance(field_rect, list):
        field_rect = []
    return PdfCandidateResponse(
        candidate_id=candidate.candidate_id,
        scan_id=candidate.scan_id,
        survey_id=candidate.survey_id,
        candidate_key=candidate.candidate_key,
        source=candidate.source,
        field_name=candidate.field_name,
        label_text=candidate.label_text,
        normalized_label=candidate.normalized_label,
        input_kind=candidate.input_kind,
        page_number=candidate.page_number,
        confidence=candidate.confidence,
        label_source=getattr(candidate, "label_source", ""),
        field_rect=[float(value) for value in field_rect if isinstance(value, int | float)],
        nearby_text=getattr(candidate, "nearby_text", ""),
        datapoint_intent=getattr(candidate, "datapoint_intent", ""),
        master_data_point_id=candidate.master_data_point_id,
        status=candidate.status,
    )


def _pdf_scan_response(scan: Any, candidates: list[Any] | None = None) -> PdfScanResponse:
    return PdfScanResponse(
        scan_id=scan.scan_id,
        survey_id=scan.survey_id,
        file_name=scan.file_name,
        file_path=scan.file_path,
        file_sha256=scan.file_sha256,
        fillable=scan.fillable,
        page_count=scan.page_count,
        candidate_count=scan.candidate_count,
        status=scan.status,
        created_at=scan.created_at.isoformat(),
        candidates=[_pdf_candidate_response(candidate) for candidate in (candidates or [])],
    )


def _master_data_point_response(row: Any) -> MasterDataPointResponse:
    return MasterDataPointResponse(
        data_point_id=row.data_point_id,
        canonical_name=row.canonical_name,
        semantic_key=row.semantic_key,
        description=row.description,
        databricks_view=row.databricks_view,
        databricks_value_column=row.databricks_value_column,
        databricks_year_column=row.databricks_year_column,
        transform_json=row.transform_json,
        status=row.status,
    )


def _master_alias_response(row: Any) -> MasterDataPointAliasResponse:
    return MasterDataPointAliasResponse(
        alias_id=row.alias_id,
        data_point_id=row.data_point_id,
        alias_text=row.alias_text,
        normalized_alias=row.normalized_alias,
        source=row.source,
    )


def _candidate_suggestions_response(row: Any) -> CandidateMappingSuggestionsResponse:
    return CandidateMappingSuggestionsResponse(
        candidate_id=row.candidate_id,
        field_name=row.field_name,
        label_text=row.label_text,
        suggestions=[
            MappingSuggestionResponse(
                data_point_id=suggestion.data_point_id,
                canonical_name=suggestion.canonical_name,
                semantic_key=suggestion.semantic_key,
                score=suggestion.score,
                reason=suggestion.reason,
            )
            for suggestion in row.suggestions
        ],
    )


def _genie_draft_item_response(row: Any) -> GenieDraftMappingItemResponse:
    return GenieDraftMappingItemResponse(
        draft_id=row.draft_id,
        candidate_id=row.candidate_id,
        field_name=row.field_name,
        label_text=row.label_text,
        provider=row.provider,
        score=row.score,
        status=row.status,
        reason=row.reason,
        master_data_point_id=row.master_data_point_id,
        databricks_view=row.databricks_view,
        databricks_value_column=row.databricks_value_column,
        databricks_year_column=row.databricks_year_column,
        transform_json=row.transform_json,
    )


def _analyst_sql_service(session: Session) -> AnalystSqlMappingService:
    settings = get_settings()
    return AnalystSqlMappingService(
        session,
        settings=settings,
        sql_reader=DatabricksSqlValueReader(settings),
        mapper=DatabricksServingSqlMapper(settings),
    )


def _analyst_sql_preview_response(row: Any) -> AnalystSqlPreviewResponse:
    return AnalystSqlPreviewResponse(
        query_id=row.query_id,
        scan_id=row.scan_id,
        name=row.name,
        survey_year=row.survey_year,
        columns=json.loads(row.columns_json or "[]"),
        sample_rows=json.loads(row.sample_rows_json or "[]"),
        row_count=row.row_count,
    )


def _analyst_sql_draft_response(row: Any, *, label_text: str = "") -> AnalystSqlMappingDraftResponse:
    return AnalystSqlMappingDraftResponse(
        draft_id=row.draft_id,
        query_id=row.query_id,
        scan_id=row.scan_id,
        candidate_id=row.candidate_id,
        field_name=row.field_name,
        label_text=label_text,
        source_row_index=row.source_row_index,
        source_column=row.source_column,
        value_preview=row.value_preview,
        confidence=row.confidence,
        reason=row.reason,
        status=row.status,
    )


@app.get("/health")
def health() -> dict[str, object]:
    settings: Settings = get_settings()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "control_plane_db": "sqlite",
        "database_url": settings.control_plane_database_url,
    }


@app.get("/integrations/databricks")
def databricks_integration_status() -> dict[str, object]:
    settings: Settings = get_settings()
    configured_with_pat = bool(settings.databricks_host and settings.databricks_token)
    configured_with_oauth_m2m = bool(
        settings.databricks_host and settings.databricks_client_id and settings.databricks_client_secret
    )

    return {
        "configured": configured_with_pat or configured_with_oauth_m2m,
        "auth_type": settings.databricks_auth_type or ("oauth-m2m" if configured_with_oauth_m2m else "pat"),
        "host": settings.databricks_host,
        "sql_warehouse_configured": bool(settings.databricks_sql_warehouse_id),
        "resolver_mode": settings.databricks_resolver_mode,
    }


class BuildPdfPageContextRequest(BaseModel):
    limit_pages: int | None = None
    force: bool = False


@app.post("/pdf-scans/{scan_id}/build-page-context")
def build_pdf_page_context(
    scan_id: str,
    request: BuildPdfPageContextRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PdfDatapointService(session)
    try:
        return service.build_pdf_page_context_cache(
            scan_id=scan_id,
            limit_pages=request.limit_pages,
            force=request.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/pdf-scans", response_model=PdfScanResponse)
def create_pdf_scan(request: PdfScanRequest, session: Session = Depends(get_session)) -> PdfScanResponse:
    service = PdfDatapointService(session)
    try:
        scan, candidates = service.scan_pdf(
            file_path=request.file_path,
            survey_id=request.survey_id,
            require_label_enrichment=request.require_label_enrichment,
            allow_enrichment_fallback=request.allow_enrichment_fallback,
            label_enrichment_candidate_limit=request.label_enrichment_candidate_limit,
        )
    except PdfLabelEnrichmentFailedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LABEL_ENRICHMENT_FAILED",
                "message": "LLM label enrichment failed before scan completion",
                "provider": exc.provider,
                "reason": exc.reason,
                "can_fallback": True,
            },
        ) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _pdf_scan_response(scan, candidates)


@app.get("/pdf-scans", response_model=list[PdfScanResponse])
def list_pdf_scans(session: Session = Depends(get_session)) -> list[PdfScanResponse]:
    service = PdfDatapointService(session)
    return [_pdf_scan_response(scan) for scan in service.list_pdf_scans()]


@app.get("/pdf-scans/{scan_id}", response_model=PdfScanResponse)
def get_pdf_scan(scan_id: str, session: Session = Depends(get_session)) -> PdfScanResponse:
    service = PdfDatapointService(session)
    try:
        scan = service.get_pdf_scan(scan_id)
        candidates = service.list_pdf_candidates(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _pdf_scan_response(scan, candidates)


@app.delete("/pdf-scans/{scan_id}", status_code=204)
def delete_pdf_scan(scan_id: str, session: Session = Depends(get_session)) -> None:
    service = PdfDatapointService(session)
    try:
        service.delete_pdf_scan(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/pdf-scans/{scan_id}/candidates", response_model=list[PdfCandidateResponse])
def list_pdf_scan_candidates(scan_id: str, session: Session = Depends(get_session)) -> list[PdfCandidateResponse]:
    service = PdfDatapointService(session)
    try:
        candidates = service.list_pdf_candidates(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_pdf_candidate_response(candidate) for candidate in candidates]


@app.get("/pdf-scans/{scan_id}/candidates/by-section", response_model=CandidatesBySectionResponse)
def list_candidates_by_section(scan_id: str, session: Session = Depends(get_session)) -> CandidatesBySectionResponse:
    service = PdfDatapointService(session)
    try:
        candidates = service.list_pdf_candidates(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    groups: dict[str, dict] = {}
    for candidate in candidates:
        section_id, section_label = cds_section_for(candidate.field_name)
        if section_id not in groups:
            groups[section_id] = {"section_id": section_id, "section_label": section_label, "candidates": []}
        groups[section_id]["candidates"].append(_pdf_candidate_response(candidate))
    sections = sorted(
        [
            CandidateSectionGroup(
                section_id=g["section_id"],
                section_label=g["section_label"],
                candidate_count=len(g["candidates"]),
                candidates=g["candidates"],
            )
            for g in groups.values()
        ],
        key=lambda s: (not s.section_id.startswith(("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")), s.section_id),
    )
    return CandidatesBySectionResponse(
        scan_id=scan_id,
        total_candidates=len(candidates),
        sections=sections,
    )


@app.get("/master-data-points", response_model=list[MasterDataPointResponse])
def list_master_data_points(session: Session = Depends(get_session)) -> list[MasterDataPointResponse]:
    service = PdfDatapointService(session)
    return [_master_data_point_response(row) for row in service.list_master_data_points()]


@app.post("/master-data-points/bootstrap-from-catalog", response_model=BootstrapMasterDataPointsResponse)
def bootstrap_master_data_points_from_catalog(
    request: BootstrapMasterDataPointsRequest,
    session: Session = Depends(get_session),
) -> BootstrapMasterDataPointsResponse:
    service = PdfDatapointService(session)
    payload = service.bootstrap_master_data_points_from_catalog(
        section_id=request.section_id,
        include_inactive=request.include_inactive,
        create_aliases=request.create_aliases,
    )
    return BootstrapMasterDataPointsResponse(
        created_count=payload.created_count,
        reused_count=payload.reused_count,
        alias_created_count=payload.alias_created_count,
        data_point_ids=payload.data_point_ids,
    )


@app.post("/master-data-points/bootstrap-from-fake-form", response_model=BootstrapMasterDataPointsResponse)
def bootstrap_master_data_points_from_fake_form(
    request: BootstrapFakeFormMasterDataPointsRequest,
    session: Session = Depends(get_session),
) -> BootstrapMasterDataPointsResponse:
    service = PdfDatapointService(session)
    try:
        payload = service.bootstrap_master_data_points_from_fake_form_data(
            file_path=request.file_path,
            create_literal_bindings=request.create_literal_bindings,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BootstrapMasterDataPointsResponse(
        created_count=payload.created_count,
        reused_count=payload.reused_count,
        alias_created_count=payload.alias_created_count,
        data_point_ids=payload.data_point_ids,
    )


@app.post("/master-data-points", response_model=MasterDataPointResponse)
def create_master_data_point(
    request: CreateMasterDataPointRequest,
    session: Session = Depends(get_session),
) -> MasterDataPointResponse:
    service = PdfDatapointService(session)
    try:
        row = service.create_master_data_point(
            canonical_name=request.canonical_name,
            semantic_key=request.semantic_key,
            description=request.description,
            databricks_view=request.databricks_view,
            databricks_value_column=request.databricks_value_column,
            databricks_year_column=request.databricks_year_column,
            transform_json=request.transform_json,
            data_point_id=request.data_point_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _master_data_point_response(row)


@app.patch("/master-data-points/{data_point_id}/binding", response_model=MasterDataPointResponse)
def update_master_data_point_binding(
    data_point_id: str,
    request: UpdateMasterDataPointBindingRequest,
    session: Session = Depends(get_session),
) -> MasterDataPointResponse:
    service = PdfDatapointService(session)
    try:
        row = service.update_master_databricks_binding(
            data_point_id=data_point_id,
            databricks_view=request.databricks_view,
            databricks_value_column=request.databricks_value_column,
            databricks_year_column=request.databricks_year_column,
            transform_json=request.transform_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _master_data_point_response(row)


@app.get("/master-data-points/{data_point_id}/aliases", response_model=list[MasterDataPointAliasResponse])
def list_master_data_point_aliases(
    data_point_id: str,
    session: Session = Depends(get_session),
) -> list[MasterDataPointAliasResponse]:
    service = PdfDatapointService(session)
    try:
        aliases = service.list_master_aliases(data_point_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_master_alias_response(alias) for alias in aliases]


@app.post("/master-data-points/{data_point_id}/aliases", response_model=MasterDataPointAliasResponse)
def create_master_data_point_alias(
    data_point_id: str,
    request: CreateMasterDataPointAliasRequest,
    session: Session = Depends(get_session),
) -> MasterDataPointAliasResponse:
    service = PdfDatapointService(session)
    try:
        alias = service.add_master_alias(
            data_point_id=data_point_id,
            alias_text=request.alias_text,
            source=request.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _master_alias_response(alias)


@app.post("/pdf-candidates/{candidate_id}/map", response_model=PdfCandidateResponse)
def map_pdf_candidate(
    candidate_id: str,
    request: MapPdfCandidateRequest,
    session: Session = Depends(get_session),
) -> PdfCandidateResponse:
    service = PdfDatapointService(session)
    try:
        candidate = service.map_pdf_candidate(
            candidate_id=candidate_id,
            master_data_point_id=request.master_data_point_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _pdf_candidate_response(candidate)


@app.get("/pdf-scans/{scan_id}/mapping-suggestions", response_model=list[CandidateMappingSuggestionsResponse])
def suggest_pdf_candidate_mappings(
    scan_id: str,
    limit_per_candidate: int = 3,
    session: Session = Depends(get_session),
) -> list[CandidateMappingSuggestionsResponse]:
    service = PdfDatapointService(session)
    try:
        suggestions = service.suggest_candidate_mappings(
            scan_id=scan_id,
            limit_per_candidate=limit_per_candidate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_candidate_suggestions_response(row) for row in suggestions]


@app.post("/pdf-scans/{scan_id}/auto-map", response_model=AutoMapPdfScanResponse)
def auto_map_pdf_scan_candidates(
    scan_id: str,
    request: AutoMapPdfScanRequest,
    session: Session = Depends(get_session),
) -> AutoMapPdfScanResponse:
    service = PdfDatapointService(session)
    try:
        payload = service.auto_map_pdf_scan_candidates(
            scan_id=scan_id,
            min_score=request.min_score,
            min_margin=request.min_margin,
            include_already_mapped=request.include_already_mapped,
            add_alias_on_map=request.add_alias_on_map,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AutoMapPdfScanResponse(
        scan_id=payload.scan_id,
        total_candidates=payload.total_candidates,
        mapped_count=payload.mapped_count,
        already_mapped_count=payload.already_mapped_count,
        skipped_no_suggestion=payload.skipped_no_suggestion,
        skipped_low_score=payload.skipped_low_score,
        skipped_ambiguous=payload.skipped_ambiguous,
        mapped_candidate_ids=payload.mapped_candidate_ids,
    )


@app.post("/pdf-scans/{scan_id}/genie-draft-mappings", response_model=GenieDraftMappingsResponse)
def generate_genie_draft_mappings(
    scan_id: str,
    request: GenieDraftMappingsRequest,
    session: Session = Depends(get_session),
) -> GenieDraftMappingsResponse:
    service = PdfDatapointService(session)
    try:
        payload = service.generate_pdf_mapping_drafts(
            scan_id=scan_id,
            min_score=request.min_score,
            include_already_mapped=request.include_already_mapped,
            limit_candidates=request.limit_candidates,
            provider=request.provider,
            overwrite_existing=request.overwrite_existing,
            genie_batch_size=request.genie_batch_size,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Unknown scan_id"):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return GenieDraftMappingsResponse(
        scan_id=payload.scan_id,
        total_candidates=payload.total_candidates,
        drafted_count=payload.drafted_count,
        skipped_count=payload.skipped_count,
        skipped_already_mapped=payload.skipped_already_mapped,
        skipped_low_score=payload.skipped_low_score,
        drafts=[_genie_draft_item_response(item) for item in payload.drafts],
    )


@app.post("/pdf-scans/{scan_id}/genie-draft-mappings/jobs", response_model=GenieDraftMappingsJobResponse)
def launch_genie_draft_mappings_job(
    scan_id: str,
    request: GenieDraftMappingsRequest,
    session: Session = Depends(get_session),
) -> GenieDraftMappingsJobResponse:
    service = PdfDatapointService(session)
    try:
        service.get_pdf_scan(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    job_id = f"pdfjob_{uuid.uuid4().hex[:12]}"
    request_payload = request.model_dump()
    request_payload["scan_id"] = scan_id
    request_payload["job_type"] = "genie_draft_mappings"
    job = WorkflowJob(
        job_id=job_id,
        status="queued",
        request_json=json.dumps(request_payload),
        steps_json="[]",
    )
    session.add(job)
    session.commit()

    payload = request.model_copy(deep=True)
    thread = threading.Thread(
        target=_execute_genie_draft_mappings_job,
        kwargs={"job_id": job_id, "scan_id": scan_id, "payload": payload},
        daemon=True,
    )
    thread.start()
    return GenieDraftMappingsJobResponse(job_id=job_id, scan_id=scan_id, status="queued")


@app.get("/pdf-scans/{scan_id}/genie-draft-mappings", response_model=list[GenieDraftMappingItemResponse])
def list_genie_draft_mappings(
    scan_id: str,
    status: str | None = None,
    limit: int = 250,
    session: Session = Depends(get_session),
) -> list[GenieDraftMappingItemResponse]:
    service = PdfDatapointService(session)
    try:
        rows = service.list_pdf_mapping_drafts(scan_id=scan_id, status=status, limit=limit)
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Unknown scan_id"):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return [_genie_draft_item_response(row) for row in rows]


@app.get("/pdf-scans/{scan_id}/genie-draft-mappings/jobs/{job_id}", response_model=GenieDraftMappingsJobStatusResponse)
def get_genie_draft_mappings_job(
    scan_id: str,
    job_id: str,
    session: Session = Depends(get_session),
) -> GenieDraftMappingsJobStatusResponse:
    job = session.get(WorkflowJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    request_payload = json.loads(job.request_json or "{}")
    if request_payload.get("job_type") != "genie_draft_mappings" or request_payload.get("scan_id") != scan_id:
        raise HTTPException(status_code=404, detail=f"Unknown job_id for scan_id: {job_id}")
    return GenieDraftMappingsJobStatusResponse(
        job_id=job.job_id,
        scan_id=scan_id,
        status=job.status,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        request=request_payload,
        steps=json.loads(job.steps_json or "[]"),
        result=json.loads(job.result_json) if job.result_json else None,
        error=job.error,
    )


@app.get("/pdf-scans/{scan_id}/genie-calls", response_model=list[GenieApiCallHistoryResponse])
def list_genie_api_calls(
    scan_id: str,
    job_id: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> list[GenieApiCallHistoryResponse]:
    service = PdfDatapointService(session)
    try:
        service.get_pdf_scan(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    safe_limit = max(1, min(2000, limit))
    query = (
        select(GenieApiCallHistory)
        .where(GenieApiCallHistory.scan_id == scan_id)
        .order_by(GenieApiCallHistory.call_id.desc())
        .limit(safe_limit)
    )
    if job_id:
        query = (
            select(GenieApiCallHistory)
            .where(GenieApiCallHistory.scan_id == scan_id)
            .where(GenieApiCallHistory.job_id == job_id)
            .order_by(GenieApiCallHistory.call_id.desc())
            .limit(safe_limit)
        )
    rows = list(session.execute(query).scalars())
    return [
        GenieApiCallHistoryResponse(
            call_id=row.call_id,
            job_id=row.job_id,
            scan_id=row.scan_id,
            provider=row.provider,
            batch_index=row.batch_index,
            status=row.status,
            request=_json_payload(row.request_json, default={}),
            response=_json_payload(row.response_json, default=None),
            error=row.error,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@app.post("/pdf-mapping-drafts/{draft_id}/approve", response_model=ApprovePdfMappingDraftResponse)
def approve_pdf_mapping_draft(
    draft_id: str,
    request: ApprovePdfMappingDraftRequest,
    session: Session = Depends(get_session),
) -> ApprovePdfMappingDraftResponse:
    service = PdfDatapointService(session)
    try:
        payload = service.approve_pdf_mapping_draft(
            draft_id=draft_id,
            apply_binding=request.apply_binding,
            overwrite_master_binding=request.overwrite_master_binding,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Unknown draft_id"):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return ApprovePdfMappingDraftResponse(
        draft=_genie_draft_item_response(payload.draft),
        candidate_id=payload.candidate_id,
        master_data_point_id=payload.master_data_point_id,
        binding_applied=payload.binding_applied,
    )


@app.post("/pdf-scans/{scan_id}/resolve-values", response_model=ResolvePdfScanResponse)
def resolve_pdf_scan_values(
    scan_id: str,
    request: ResolvePdfScanRequest,
    session: Session = Depends(get_session),
) -> ResolvePdfScanResponse:
    service = PdfDatapointService(session)
    try:
        payload = service.resolve_mapped_pdf_scan(scan_id=scan_id, survey_year=request.survey_year)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ResolvePdfScanResponse(
        scan_id=payload.scan_id,
        survey_year=payload.survey_year,
        values=payload.values,
        missing_candidates=payload.missing_candidates,
        unmapped_candidates=payload.unmapped_candidates,
    )


@app.post("/pdf-scans/{scan_id}/resolve-via-genie", response_model=GenieResolutionResult)
def resolve_pdf_scan_via_genie(
    scan_id: str,
    request: GenieResolveRequest,
    session: Session = Depends(get_session),
) -> GenieResolutionResult:
    service = PdfDatapointService(session)
    try:
        result = service.resolve_pdf_scan_via_genie(
            scan_id=scan_id,
            survey_year=request.survey_year,
            batch_size=request.batch_size,
            min_confidence=request.min_confidence,
            force_regenie=request.force_regenie,
            page_numbers=request.page_numbers,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GenieResolutionResult(**result)


@app.post("/pdf-scans/{scan_id}/resolve-direct", response_model=DirectResolutionResult)
def resolve_pdf_scan_direct(
    scan_id: str,
    request: DirectResolveRequest,
    session: Session = Depends(get_session),
) -> DirectResolutionResult:
    service = PdfDatapointService(session)
    try:
        result = service.resolve_pdf_scan_direct(
            scan_id=scan_id,
            survey_year=request.survey_year,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DirectResolutionResult(**result)


@app.get("/pdf-scans/{scan_id}/resolved-values", response_model=list[ResolvedValueResponse])
def list_resolved_values(
    scan_id: str,
    session: Session = Depends(get_session),
) -> list[ResolvedValueResponse]:
    service = PdfDatapointService(session)
    rows = service.list_resolved_values(scan_id)
    return [
        ResolvedValueResponse(
            candidate_id=r.candidate_id,
            field_name=r.field_name,
            label_text=r.label_text,
            section=_extract_section_label(r.nearby_text),
            datapoint_intent=r.datapoint_intent,
            genie_value=r.genie_value,
            genie_confidence=r.genie_confidence,
            genie_sql_template=r.genie_sql_template,
            genie_table=r.genie_table,
            genie_column=r.genie_column,
            genie_reason=r.genie_reason,
            genie_resolved_at=r.genie_resolved_at.isoformat() if r.genie_resolved_at else None,
            direct_sql_failures=r.direct_sql_failures,
            status=r.status,
        )
        for r in rows
    ]


@app.post("/pdf-scans/{scan_id}/analyst-sql/preview", response_model=AnalystSqlPreviewResponse)
def preview_analyst_sql(
    scan_id: str,
    request: AnalystSqlPreviewRequest,
    session: Session = Depends(get_session),
) -> AnalystSqlPreviewResponse:
    service = _analyst_sql_service(session)
    try:
        query = service.preview_sql(
            scan_id=scan_id,
            name=request.name,
            sql_text=request.sql_text,
            survey_year=request.survey_year,
            row_limit=request.row_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404 if str(exc).startswith("Unknown scan_id") else 400, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _analyst_sql_preview_response(query)


@app.post("/analyst-sql/{query_id}/auto-map", response_model=AnalystSqlAutoMapResponse)
def auto_map_analyst_sql(
    query_id: str,
    request: AnalystSqlAutoMapRequest,
    session: Session = Depends(get_session),
) -> AnalystSqlAutoMapResponse:
    service = _analyst_sql_service(session)
    try:
        drafts = service.auto_map(query_id=query_id, max_drafts=request.max_drafts, section_filter=request.section_filter)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    candidate_labels: dict[str, str] = {}
    if drafts:
        candidate_labels = {
            row.candidate_id: row.label_text
            for row in session.execute(
                select(SurveyPdfDataPointCandidate).where(
                    SurveyPdfDataPointCandidate.candidate_id.in_([d.candidate_id for d in drafts])
                )
            ).scalars()
        }
    query = session.get(AnalystSqlQuery, query_id)
    scan_id = query.scan_id if query else (drafts[0].scan_id if drafts else "")
    return AnalystSqlAutoMapResponse(
        query_id=query_id,
        scan_id=scan_id,
        drafted_count=len(drafts),
        drafts=[_analyst_sql_draft_response(draft, label_text=candidate_labels.get(draft.candidate_id, "")) for draft in drafts],
    )


@app.post(
    "/analyst-sql-mapping-drafts/{draft_id}/approve",
    response_model=ApproveAnalystSqlMappingDraftResponse,
)
def approve_analyst_sql_mapping_draft(
    draft_id: str,
    request: ApproveAnalystSqlMappingDraftRequest,
    session: Session = Depends(get_session),
) -> ApproveAnalystSqlMappingDraftResponse:
    service = _analyst_sql_service(session)
    try:
        draft, mapping, value = service.approve_draft(
            draft_id=draft_id,
            source_row_index=request.source_row_index,
            source_column=request.source_column,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404 if str(exc).startswith("Unknown") else 400, detail=str(exc)) from exc
    return ApproveAnalystSqlMappingDraftResponse(
        draft_id=draft.draft_id,
        mapping_id=mapping.mapping_id,
        query_id=draft.query_id,
        scan_id=draft.scan_id,
        candidate_id=draft.candidate_id,
        field_name=draft.field_name,
        value=value,
        status=draft.status,
    )


@app.post("/analyst-sql/{query_id}/rerun-approved", response_model=AnalystSqlRerunResponse)
def rerun_approved_analyst_sql(
    query_id: str,
    request: AnalystSqlRerunRequest,
    session: Session = Depends(get_session),
) -> AnalystSqlRerunResponse:
    service = _analyst_sql_service(session)
    try:
        result = service.rerun_approved(query_id=query_id, survey_year=request.survey_year)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    query = session.get(AnalystSqlQuery, query_id)
    scan_id = query.scan_id if query else ""
    return AnalystSqlRerunResponse(
        query_id=query_id,
        scan_id=scan_id,
        survey_year=request.survey_year,
        refreshed=result["refreshed"],
        missing=result["missing"],
        sql_errors=result["sql_errors"],
    )


@app.post("/pdf-scans/{scan_id}/export-filled-pdf", response_model=FilledPdfExportResponse)
def export_filled_pdf(
    scan_id: str,
    request: FilledPdfExportRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FilledPdfExportResponse:
    service = PdfDatapointService(session, settings=settings)
    try:
        result = service.export_resolved_values_to_pdf(
            scan_id=scan_id,
            output_file_path=request.output_file_path,
            flatten=request.flatten,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("Unknown scan_id") else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    output_path = Path(result.output_file_path).resolve()
    export_dir = Path(settings.pdf_export_dir).resolve()
    download_url = f"/pdf-exports/{output_path.name}" if output_path.parent == export_dir else ""
    return FilledPdfExportResponse(
        scan_id=result.scan_id,
        source_file_path=result.source_file_path,
        output_file_path=result.output_file_path,
        download_url=download_url,
        filled_count=result.filled_count,
        skipped_count=result.skipped_count,
        missing_pdf_fields=result.missing_pdf_fields,
    )


@app.get("/pdf-exports/{file_name}")
def download_pdf_export(
    file_name: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    safe_name = Path(file_name).name
    if safe_name != file_name:
        raise HTTPException(status_code=404, detail="Unknown PDF export")
    export_path = (Path(settings.pdf_export_dir) / safe_name).resolve()
    export_dir = Path(settings.pdf_export_dir).resolve()
    if export_path.parent != export_dir or not export_path.exists() or not export_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown PDF export")
    return FileResponse(
        str(export_path),
        media_type="application/pdf",
        filename=safe_name,
    )


def _extract_section_label(nearby_text: str) -> str:
    import re as _re
    match = _re.search(r"Section:\s*(.+?)(?:\s*\||\n|$)", nearby_text or "")
    return match.group(1).strip() if match else ""


@app.post("/pdf-scans/{scan_id}/publish-field-catalog", response_model=list[CatalogFieldResponse])
def publish_pdf_scan_field_catalog(
    scan_id: str,
    request: PublishPdfScanCatalogRequest,
    session: Session = Depends(get_session),
) -> list[CatalogFieldResponse]:
    service = PdfDatapointService(session)
    try:
        rows = service.publish_pdf_scan_to_field_catalog(
            scan_id=scan_id,
            section_id=request.section_id,
            overwrite=request.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        CatalogFieldResponse(
            field_id=row.field_id,
            section_id=row.section_id,
            label_text=row.label_text,
            input_kind=row.input_kind,
            required_flag=row.required_flag,
            databricks_view=row.databricks_view,
            databricks_value_column=row.databricks_value_column,
            databricks_year_column=row.databricks_year_column,
            transform_json=row.transform_json,
            status=row.status,
        )
        for row in rows
    ]


# ── Shared UI helpers ─────────────────────────────────────────────────────────
_SHARED_CSS = """
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    :root {
      --bg: #f1f5f9;
      --surface: #ffffff;
      --surface-2: #f8fafc;
      --border: #cbd5e1;
      --border-soft: #e2e8f0;
      --ink: #0f172a;
      --ink-2: #1e293b;
      --muted: #334155;
      --accent: #2563eb;
      --accent-2: #1d4ed8;
      --accent-bg: #eff6ff;
      --accent-border: #bfdbfe;
      --amber: #b45309;
      --amber-bg: #fffbeb;
      --amber-border: #fcd34d;
      --good: #15803d;
      --good-bg: #f0fdf4;
      --good-border: #86efac;
      --bad: #b91c1c;
      --bad-bg: #fef2f2;
      --bad-border: #fecaca;
      --code-bg: #0f172a;
      --code-fg: #e2e8f0;
      --shadow-sm: 0 1px 2px rgba(15,23,42,.06);
      --shadow: 0 4px 16px rgba(15,23,42,.08);
      --shadow-lg: 0 12px 32px rgba(15,23,42,.12);
      --radius-sm: 8px;
      --radius: 12px;
      --radius-lg: 16px;
    }
    html { font-size: 15px; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.5;
      min-height: 100vh;
    }
    /* ── Nav ── */
    .topnav {
      position: sticky; top: 0; z-index: 100;
      background: rgba(255,255,255,.96);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 0;
      padding: 0 24px;
      height: 52px;
    }
    .topnav-brand {
      font-weight: 800; font-size: 15px; color: var(--ink);
      display: flex; align-items: center; gap: 8px;
      text-decoration: none; margin-right: 32px; flex-shrink: 0;
    }
    .topnav-brand svg { color: var(--accent); }
    .topnav-links { display: flex; align-items: center; gap: 2px; flex: 1; }
    .topnav-links a {
      padding: 6px 12px; border-radius: var(--radius-sm);
      font-size: 13.5px; font-weight: 500; color: var(--muted);
      text-decoration: none; transition: all .15s;
      white-space: nowrap;
    }
    .topnav-links a:hover { background: var(--surface-2); color: var(--ink); }
    .topnav-links a.active {
      color: var(--accent); font-weight: 700;
      background: var(--accent-bg);
    }
    .topnav-right { display: flex; align-items: center; gap: 10px; margin-left: auto; }
    .health-dot {
      width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
      box-shadow: 0 0 0 3px rgba(34,197,94,.2);
    }
    /* ── Layout ── */
    .page { max-width: 1280px; margin: 0 auto; padding: 28px 24px 64px; }
    .page-header { margin-bottom: 28px; }
    .page-header h1 { font-size: clamp(22px, 3.5vw, 32px); font-weight: 800; letter-spacing: -.03em; margin: 0 0 6px; }
    .page-header p { color: var(--muted); margin: 0; font-size: 15px; max-width: 720px; line-height: 1.6; }
    .page-header a { color: var(--accent-2); font-weight: 600; text-decoration: none; }
    .page-header a:hover { text-decoration: underline; }
    .two-col { display: grid; grid-template-columns: minmax(0,1.1fr) minmax(340px,.9fr); gap: 20px; align-items: start; }
    .stack { display: flex; flex-direction: column; gap: 16px; }
    /* ── Card ── */
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius-lg); box-shadow: var(--shadow-sm);
      padding: 20px 22px;
    }
    .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
    .step-badge {
      width: 36px; height: 36px; border-radius: 12px; flex-shrink: 0;
      background: var(--accent-2); color: #ffffff;
      display: grid; place-items: center; font-weight: 800; font-size: 15px;
    }
    .card-header-text h2 { font-size: 16px; font-weight: 700; margin: 0 0 2px; letter-spacing: -.02em; }
    .card-header-text p { font-size: 12.5px; color: var(--muted); margin: 0; line-height: 1.4; }
    /* ── Stat row ── */
    .stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }
    .stat {
      background: var(--surface-2); border: 1px solid var(--border-soft);
      border-radius: var(--radius); padding: 12px 14px;
    }
    .stat strong { display: block; font-size: 26px; font-weight: 800; letter-spacing: -.04em; color: var(--ink); }
    .stat span { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
    /* ── Form ── */
    .field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .field { display: flex; flex-direction: column; gap: 5px; }
    .field.span2 { grid-column: span 2; }
    label.field-label {
      font-size: 12px; font-weight: 700; color: var(--ink-2);
      text-transform: uppercase; letter-spacing: .06em; display: flex; align-items: center; gap: 6px;
    }
    .tip-icon {
      display: inline-flex; align-items: center; justify-content: center;
      width: 16px; height: 16px; border-radius: 50%;
      background: var(--surface-2); border: 1px solid var(--border);
      font-size: 10px; color: var(--muted); cursor: help;
      position: relative;
    }
    .tip-icon .tip-text {
      display: none; position: absolute; left: 20px; top: -4px;
      width: 240px; background: #1e293b; color: #f8fafc;
      border-radius: var(--radius-sm); padding: 8px 10px; font-size: 12px;
      font-weight: 400; line-height: 1.4; z-index: 20; text-transform: none; letter-spacing: 0;
      white-space: normal; pointer-events: none;
    }
    .tip-icon:hover .tip-text, .tip-icon:focus .tip-text { display: block; }
    input, select, textarea {
      width: 100%; padding: 9px 12px;
      border: 1px solid var(--border); border-radius: var(--radius-sm);
      background: #ffffff; color: var(--ink);
      font-family: inherit; font-size: 14px; line-height: 1.4;
      transition: border-color .15s, box-shadow .15s;
      appearance: none;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-bg);
    }
    select[size] { min-height: 130px; border-radius: var(--radius); }
    .checkbox-row { display: flex; align-items: center; gap: 8px; font-size: 13.5px; }
    .checkbox-row input[type=checkbox] { width: 16px; height: 16px; accent-color: var(--accent); flex-shrink: 0; }
    /* ── Buttons ── */
    .btn-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
    button, .btn {
      display: inline-flex; align-items: center; gap: 7px;
      padding: 9px 16px; border-radius: var(--radius-sm);
      font-family: inherit; font-size: 13.5px; font-weight: 700;
      cursor: pointer; border: 1px solid transparent; transition: all .15s;
      line-height: 1; white-space: nowrap;
    }
    .btn-primary {
      background: var(--accent-2); color: #ffffff; border-color: var(--accent-2);
    }
    .btn-primary:hover { background: var(--accent); border-color: var(--accent); }
    .btn-secondary {
      background: #ffffff; color: var(--ink-2); border-color: var(--border);
    }
    .btn-secondary:hover { background: var(--accent-bg); }
    .btn-ghost {
      background: transparent; color: var(--muted); border-color: var(--border);
    }
    .btn-ghost:hover { background: var(--surface-2); color: var(--ink); }
    .btn-danger {
      background: var(--bad-bg); color: var(--bad); border-color: var(--bad-border);
    }
    button:disabled { opacity: .45; cursor: not-allowed; }
    .btn-launch {
      padding: 11px 22px; font-size: 15px;
      background: var(--accent-2); color: #ffffff; border-color: var(--accent-2);
    }
    .btn-launch:hover:not(:disabled) { background: var(--accent); border-color: var(--accent); }
    /* ── Badges / Pills ── */
    .pill {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 2px 9px; border-radius: 999px;
      font-size: 11.5px; font-weight: 700; line-height: 1.6;
    }
    .pill-good { background: var(--good-bg); color: var(--good); border: 1px solid var(--good-border); }
    .pill-warn { background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-border); }
    .pill-bad  { background: var(--bad-bg);  color: var(--bad);  border: 1px solid var(--bad-border); }
    .pill-neutral { background: #f1f5f9; color: #475569; border: 1px solid var(--border); }
    .pill-accent { background: var(--accent-bg); color: var(--accent-2); border: 1px solid var(--accent-border); }
    /* ── Status messages ── */
    .status-line {
      min-height: 18px; font-size: 13px; margin-top: 10px;
      padding: 6px 10px; border-radius: var(--radius-sm);
      display: none;
    }
    .status-line.show { display: block; }
    .status-line.ok  { background: var(--good-bg); color: var(--good); border: 1px solid var(--good-border); }
    .status-line.err { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-border); font-weight: 600; }
    .status-line.info { background: var(--accent-bg); color: var(--accent-2); border: 1px solid var(--accent-border); }
    /* ── Banner ── */
    .banner {
      border-radius: var(--radius-sm); padding: 12px 16px; font-size: 13.5px;
      display: flex; align-items: flex-start; gap: 10px; margin-bottom: 16px; display: none;
    }
    .banner.show { display: flex; }
    .banner.info  { background: var(--accent-bg); border: 1px solid var(--accent-border); color: var(--accent-2); }
    .banner.ok    { background: var(--good-bg); border: 1px solid var(--good-border); color: var(--good); }
    .banner.error { background: var(--bad-bg); border: 1px solid var(--bad-border); color: var(--bad); }
    .banner-icon { font-size: 18px; flex-shrink: 0; }
    .banner-text strong { display: block; font-weight: 700; margin-bottom: 2px; }
    /* ── Table ── */
    .table-wrap {
      overflow: auto; max-height: 460px;
      border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--surface);
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: auto; }
    thead { position: sticky; top: 0; z-index: 2; }
    th {
      background: #f8fafc; font-weight: 700; font-size: 11.5px;
      text-transform: uppercase; letter-spacing: .06em; color: #334155;
      padding: 9px 12px; text-align: left;
      border-bottom: 1px solid var(--border);
    }
    td { padding: 9px 12px; border-bottom: 1px solid var(--border-soft); vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #f8fafc; }
    .td-mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #0f172a; }
    .td-muted { color: var(--muted); font-size: 12px; }
    .empty-row td { text-align: center; color: var(--muted); padding: 28px; font-size: 13px; }
    /* ── Code/SQL ── */
    pre {
      background: var(--code-bg); color: var(--code-fg);
      border-radius: var(--radius-sm); padding: 12px 14px;
      font-family: 'JetBrains Mono', monospace; font-size: 12px;
      white-space: pre-wrap; overflow: auto; max-height: 220px; margin: 8px 0 0;
    }
    details.sql > summary {
      cursor: pointer; color: var(--accent-2); font-size: 12px; font-weight: 700;
      list-style: none; display: flex; align-items: center; gap: 5px;
    }
    details.sql > summary::before { content: '▶'; font-size: 9px; }
    details.sql[open] > summary::before { content: '▼'; }
    /* ── Accordion ── */
    details.accordion {
      border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden;
    }
    details.accordion > summary {
      cursor: pointer; padding: 10px 14px;
      background: var(--surface-2); font-size: 13px; font-weight: 700;
      color: var(--ink-2); list-style: none;
      display: flex; align-items: center; gap: 6px;
    }
    details.accordion > summary::before { content: '▶'; font-size: 9px; color: var(--muted); }
    details.accordion[open] > summary::before { content: '▼'; }
    details.accordion .accordion-body { padding: 14px; }
    /* ── Progress stepper ── */
    .stepper { display: flex; align-items: center; gap: 0; margin: 14px 0; }
    .step-dot-wrap { display: flex; flex-direction: column; align-items: center; flex: 1; }
    .step-dot {
      width: 30px; height: 30px; border-radius: 50%;
      border: 2px solid var(--border); background: var(--surface);
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 800; color: var(--muted); transition: all .25s;
    }
    .step-dot.active { border-color: var(--accent); background: var(--accent); color: #fff; }
    .step-dot.done   { border-color: #22c55e; background: #22c55e; color: #fff; }
    .step-dot.failed { border-color: var(--bad); background: var(--bad-bg); color: var(--bad); }
    .step-label { font-size: 11px; color: var(--muted); margin-top: 4px; text-align: center; }
    .step-line { flex: 1; height: 2px; background: var(--border); margin-top: -16px; }
    .step-line.done { background: #22c55e; }
    /* ── Log ── */
    .log-box {
      background: var(--code-bg); color: var(--code-fg);
      font-family: 'JetBrains Mono', monospace; font-size: 12px;
      padding: 14px; border-radius: var(--radius-sm);
      max-height: 260px; overflow-y: auto; white-space: pre-wrap;
      line-height: 1.55;
    }
    /* ── Spinner ── */
    .spinner {
      width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.35);
      border-top-color: #fff; border-radius: 50%;
      animation: spin .65s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    /* ── Section header ── */
    .section-sep { margin: 20px 0 10px; }
    .section-sep h3 {
      font-size: 12px; font-weight: 700; color: var(--muted);
      text-transform: uppercase; letter-spacing: .08em; margin: 0;
    }
    /* ── Filter chips ── */
    .chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
    .chip {
      padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 700;
      border: 1px solid var(--border); background: #ffffff; color: var(--muted);
      cursor: pointer; transition: all .15s;
    }
    .chip.active { background: var(--accent-2); color: #fff; border-color: var(--accent-2); }
    /* ── History table badges ── */
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
    .badge-queued   { background: #e2e8f0; color: #334155; }
    .badge-running  { background: var(--accent-bg); color: var(--accent-2); }
    .badge-completed{ background: var(--good-bg); color: var(--good); }
    .badge-failed   { background: var(--bad-bg); color: var(--bad); }
    /* ── Inline edit ── */
    .inline-edit { display: flex; gap: 6px; align-items: center; }
    .inline-edit input { flex: 1; padding: 5px 9px; font-size: 12px; }

    /* ── Catalog / callout ── */
    .callout {
      background: var(--accent-bg); border: 1px solid var(--accent-border);
      border-radius: var(--radius); padding: 14px 18px; margin-bottom: 18px; font-size: 14px;
      color: var(--ink-2); line-height: 1.55;
    }
    .callout-list { margin: 8px 0 12px 18px; color: var(--muted); }
    .callout-links { display: flex; gap: 8px; flex-wrap: wrap; }
    .info-panel {
      padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--border);
      border-radius: var(--radius-sm); font-size: 12.5px; line-height: 1.55;
    }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
    .toolbar input { flex: 1; min-width: 200px; }
    .toolbar-card { margin-bottom: 16px; padding: 14px 18px; }
    .catalog-table { max-height: 620px; }
    .page-meta { margin-top: 8px; font-size: 12px; color: var(--muted); }
    .flow-tag {
      display: inline-flex; padding: 2px 8px; border-radius: 999px; font-size: 10.5px;
      font-weight: 800; letter-spacing: .04em; text-transform: uppercase; margin-right: 4px;
    }
    .flow-tag-pdf { background: #e8f0ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
    .flow-tag-web { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
    .flow-tag-db { background: #f5f3ff; color: #5b21b6; border: 1px solid #ddd6fe; }
    .value-empty { color: var(--muted); }
    .value-ready { color: #15803d; }

    /* ── Responsive ── */
    @media (max-width: 900px) {
      .two-col { grid-template-columns: 1fr; }
      .stat-row { grid-template-columns: repeat(2, 1fr); }
      .field-grid { grid-template-columns: 1fr; }
      .field.span2 { grid-column: span 1; }
    }
  </style>
"""


def _nav(active: str) -> str:
    links = [
        ("/data-points", "📊 Data Points"),
        ("/pdf-ops", "📄 PDF Flow"),
        ("/pdf-vision-ops", "🖼️ PDF Vision"),
        ("/website-ops", "🌐 Web Fill"),
        ("/website-automation", "🤖 Website Automation"),
    ]
    items = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in links
    )
    return f"""
  <nav class="topnav" aria-label="Main navigation">
    <a class="topnav-brand" href="/data-points">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h7"/></svg>
      Survey Automation
    </a>
    <div class="topnav-links">{items}</div>
    <div class="topnav-right">
      <span class="health-dot" title="Control plane healthy"></span>
    </div>
  </nav>"""


_SHARED_JS = """
function escHtml(v) {
  return String(v||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function parseMaybeJson(v) {
  if (!v) return '';
  try { return JSON.parse(v); } catch { return v; }
}
async function apiFetch(path, opts={}) {
  const method = opts.method || 'GET';
  const res = await fetch(path, opts);
  const raw = await res.text();
  let body;
  try { body = raw ? JSON.parse(raw) : {}; } catch { body = raw; }
  if (!res.ok) {
    const e = Object.assign(new Error(`HTTP ${res.status} ${method} ${path}`),
      { status: res.status, method, path, body, rawBody: raw, requestBody: opts.body || '' });
    throw e;
  }
  return body;
}
"""


@app.get("/pdf-ops", response_class=HTMLResponse)
def pdf_ops_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Survey Operations — Survey Automation</title>
  <meta name="description" content="Scan a survey PDF, resolve values with Databricks Genie, and export a filled PDF." />
""" + _SHARED_CSS + """
</head>
<body>
""" + _nav("/pdf-ops") + """
<div class="page">
  <div class="page-header">
    <h1>PDF Survey Operations</h1>
    <p>Scan a fillable survey PDF, let Genie resolve each field from Databricks, review the values, then export a filled PDF. Steps run left-to-right.</p>
  </div>
  <div class="callout">
    <strong>Shared fill catalog</strong> — review PDF and Web fill values together on the
    <a href="/data-points">Data Points</a> page before exporting or running a web fill.
  </div>


  <div class="two-col">
    <!-- LEFT COLUMN -->
    <div class="stack">

      <!-- Step 1: Scan -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">1</div>
          <div class="card-header-text">
            <h2>Scan the PDF</h2>
            <p>Extract all fillable AcroForm fields and ask the label model to explain what each field means.</p>
          </div>
        </div>
        <div class="field-grid">
          <div class="field">
            <label class="field-label">PDF file path
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">Path inside the API container. For the bundled upload use /app/uploads/survey.pdf</span>
              </span>
            </label>
            <input id="filePath" value="/app/uploads/survey.pdf" />
          </div>
          <div class="field">
            <label class="field-label">Survey name
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">A short identifier for this scan run, e.g. cds_2024.</span>
              </span>
            </label>
            <input id="surveyId" value="cds_2024" />
          </div>
        </div>
        <details class="accordion" style="margin-top:12px">
          <summary>Advanced scan settings</summary>
          <div class="accordion-body">
            <div class="field">
              <label class="field-label">Max fields to enrich with label model</label>
              <input id="labelEnrichLimit" type="number" value="2000" min="1" max="2000" />
              <span style="font-size:12px;color:var(--muted);margin-top:4px;display:block">Lower only when testing quickly. Use 2000 for full production scans.</span>
            </div>
          </div>
        </details>
        <div class="btn-row">
          <button class="btn btn-primary" onclick="createScan()">Scan PDF</button>
          <button class="btn btn-secondary" onclick="loadScans()">Refresh scans</button>
        </div>
        <div style="margin-top:14px">
          <label class="field-label">Choose a scanned PDF
            <span class="tip-icon" tabindex="0">?
              <span class="tip-text">Select the scan to resolve or export from. Each scan corresponds to one PDF upload.</span>
            </span>
          </label>
          <select id="scanSelect" size="5" onchange="selectScan(this.value)"></select>
        </div>
        <div class="btn-row" style="margin-top:8px">
          <button class="btn btn-danger" onclick="deleteSelectedScan()">Delete selected scan</button>
        </div>
        <div id="scanStatus" class="status-line"></div>
      </div>

      <!-- Step 2: Resolve -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">2</div>
          <div class="card-header-text">
            <h2>Resolve values with Genie</h2>
            <p>Genie reads each field description and writes Databricks SQL. The SQL is stored for reuse across survey years.</p>
          </div>
        </div>
        <div class="field-grid">
          <div class="field">
            <label class="field-label">Survey year
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">The year Genie queries in Databricks. SQL is stored with __SURVEY_YEAR__ so you can re-run for future years without calling Genie again.</span>
              </span>
            </label>
            <input id="genieYear" type="number" value="2024" />
          </div>
          <div class="field">
            <label class="field-label">Fields per Genie request
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">10-15 is safest for enrollment fields. Larger batches can cause Genie to return wrong values.</span>
              </span>
            </label>
            <input id="genieBatchSize" type="number" value="15" min="1" max="100" />
          </div>
          <div class="field">
            <label class="field-label">Min confidence (0-100)
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">Values below this threshold are flagged as low-confidence instead of resolved-ready.</span>
              </span>
            </label>
            <input id="genieMinConf" type="number" value="60" min="0" max="100" />
          </div>
          <div class="field">
            <label class="field-label">Re-run already resolved fields</label>
            <select id="genieForce">
              <option value="false">No - skip completed fields</option>
              <option value="true">Yes - overwrite with fresh Genie results</option>
            </select>
          </div>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" onclick="resolveViaGenie()">Resolve via Genie</button>
          <button class="btn btn-secondary" onclick="resolveDirect()" title="Re-runs stored SQL for the selected year. Does not call Genie.">Refresh Direct SQL</button>
        </div>
        <div id="genieStatus" class="status-line"></div>
      </div>

      <!-- Step 4: Export -->
      <div class="card" data-endpoint="/analyst-sql/preview">
        <div class="card-header">
          <div class="step-badge">3</div>
          <div class="card-header-text">
            <h2>Analyst SQL Mapping</h2>
            <p>Paste analyst-owned Databricks SQL, preview the result, let Databricks Serving Claude Sonnet 4.6 propose field mappings, then approve what should be filled.</p>
          </div>
        </div>
        <div class="stat-row" style="margin-bottom:12px">
          <div class="stat"><strong id="metricSqlRows">0</strong><span>SQL rows</span></div>
          <div class="stat"><strong id="metricSqlDrafts">0</strong><span>Ready for review</span></div>
          <div class="stat"><strong id="metricSqlApproved">0</strong><span>Approved</span></div>
        </div>
        <div class="field-grid">
          <div class="field">
            <label class="field-label">Mapping name</label>
            <input id="analystSqlName" value="Analyst SQL mapping" />
          </div>
          <div class="field">
            <label class="field-label">Preview row limit</label>
            <input id="analystSqlLimit" type="number" value="100" min="1" max="5000" />
          </div>
        </div>
        <div class="field" style="margin-top:10px">
          <label class="field-label">Analyst SQL
            <span class="tip-icon" tabindex="0">?
              <span class="tip-text">SQL can return readable columns and rows. It does not need to return pdf_field/value. The system asks the model to propose mappings, then waits for analyst approval.</span>
            </span>
          </label>
          <textarea id="analystSqlText" rows="8" style="width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid var(--border);border-radius:14px;padding:12px;background:var(--surface);color:var(--text)">SELECT metric, men, women, total
FROM your_catalog.your_schema.your_table</textarea>
        </div>
        <div class="field" style="margin-bottom:8px">
          <label class="field-label">CDS section to map
            <span class="tip-icon" tabindex="0">?
              <span class="tip-text">Scopes the candidates sent to the model to only one CDS section. Highly recommended — sending all 1000+ candidates defeats the model. Select the section that matches your SQL.</span>
            </span>
          </label>
          <select id="analystSqlSectionFilter" style="width:100%;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font-size:13px">
            <option value="">All candidates (not recommended for large scans)</option>
          </select>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary" onclick="previewAnalystSql()">Preview SQL</button>
          <button class="btn btn-primary" onclick="autoMapAnalystSql()">Auto-map with Sonnet</button>
          <button class="btn btn-secondary" onclick="rerunApprovedSql()">Rerun approved SQL</button>
        </div>
        <div id="analystSqlStatus" class="status-line"></div>
        <div class="table-wrap" style="margin-top:12px">
          <table>
            <thead><tr><th>PDF field</th><th>Proposed source</th><th>Value</th><th>Confidence</th><th>Action</th></tr></thead>
            <tbody id="analystSqlRows"><tr class="empty-row"><td colspan="5">Preview SQL, then click Auto-map with Sonnet.</td></tr></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="step-badge">4</div>
          <div class="card-header-text">
            <h2>Export filled PDF</h2>
            <p>Write resolved values into the AcroForm fields of the source PDF and download the result.</p>
          </div>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" onclick="exportFilledPdf()">Export Filled PDF</button>
        </div>
        <div id="pdfExportStatus" class="status-line"></div>
        <div id="pdfExportLink" style="margin-top:8px;font-size:13px"></div>
      </div>

    </div>
    <!-- RIGHT COLUMN -->
    <div class="stack">

      <!-- Step 3: Review -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">3</div>
          <div class="card-header-text">
            <h2>Review what will be used</h2>
            <p>Inspect every field found in the PDF, grouped by CDS section, and the resolved values before exporting.</p>
          </div>
        </div>
        <div class="stat-row">
          <div class="stat"><strong id="metricScans">0</strong><span>Scans</span></div>
          <div class="stat"><strong id="metricFields">0</strong><span>PDF fields</span></div>
          <div class="stat"><strong id="metricLabeled">0</strong><span>Explained</span></div>
          <div class="stat"><strong id="metricResolved">0</strong><span>Resolved</span></div>
        </div>
        <div class="field-grid" style="margin-bottom:10px">
          <div class="field">
            <input id="fieldSearch" placeholder="Search field, label or page..." oninput="renderCandidates()" />
          </div>
          <div class="field">
            <select id="fieldFilter" onchange="renderCandidates()">
              <option value="all">All PDF fields</option>
              <option value="explained">Explained by label model</option>
              <option value="needs_label">Needs label explanation</option>
            </select>
          </div>
        </div>
        <div class="btn-row" style="margin-top:0">
          <button class="btn btn-secondary" onclick="loadSelectedScan()">Reload PDF fields</button>
          <button class="btn btn-primary" onclick="loadResolvedValues()">Load resolved values</button>
        </div>
        <div id="reviewStatus" class="status-line"></div>

        <div class="section-sep"><h3>PDF fields by CDS section</h3></div>
        <div id="candidateSections"><p style="color:var(--text-muted);font-size:13px;padding:8px 0">Select a scan above to see fields grouped by section.</p></div>

        <div class="section-sep"><h3>Resolved values (Genie + SQL)</h3></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Field</th><th>Value</th><th>Status</th><th>Reason &amp; SQL</th></tr></thead>
            <tbody id="resolvedRows"><tr class="empty-row"><td colspan="4">Run Genie resolve, then click Load resolved values.</td></tr></tbody>
          </table>
        </div>
      </div>

      <!-- Debug: collapsible -->
      <details class="accordion" id="debugAccordion">
        <summary>&#x1F6E0; Debug - Raw API responses</summary>
        <div class="accordion-body">
          <p style="font-size:12px;color:var(--muted);margin:0 0 8px">Raw backend errors and last API results appear here. Collapse when not debugging.</p>
          <div class="section-sep"><h3>Last error</h3></div>
          <pre id="errorLog" style="min-height:48px">No backend errors yet.</pre>
          <div class="section-sep"><h3>Last API result</h3></div>
          <pre id="resultBox" style="min-height:48px">{}</pre>
        </div>
      </details>

    </div>
  </div>
</div>

<script>
""" + _SHARED_JS + """
let scans = [], candidates = [], resolvedValues = [], selectedScanId = \'\';
let analystSqlQueryId = \'\', analystSqlDrafts = [], analystSqlApprovedCount = 0;
const $ = id => document.getElementById(id);

function statusEl(id, text, type=\'ok\') {
  const el = $(id);
  if (!text) { el.className=\'status-line\'; el.textContent=\'\'; return; }
  el.className = `status-line show ${type}`;
  el.textContent = text;
}

function dump(val) { $(\'resultBox\').textContent = JSON.stringify(val, null, 2); }

function showErr(statusId, err) {
  const payload = {
    error: err.message || String(err),
    status: err.status || null,
    method: err.method || null,
    path: err.path || null,
    request_body: parseMaybeJson(err.requestBody || \'\'),
    response_body: err.body !== undefined ? err.body : err.rawBody,
  };
  $(\'errorLog\').textContent = JSON.stringify(payload, null, 2);
  $(\'debugAccordion\').open = true;
  statusEl(statusId, `Error: ${err.message}`, \'err\');
}

async function loadScans() {
  try {
    scans = await apiFetch(\'/pdf-scans\');
    $(\'metricScans\').textContent = String(scans.length);
    $(\'scanSelect\').innerHTML = scans.map(s =>
      `<option value="${escHtml(s.scan_id)}">${escHtml(s.survey_id)} &middot; ${s.candidate_count} fields &middot; ${escHtml(s.scan_id.slice(-8))}</option>`
    ).join(\'\');
    if (!selectedScanId && scans.length) await selectScan(scans[0].scan_id);
    statusEl(\'scanStatus\', `${scans.length} scan(s) loaded.`, \'ok\');
  } catch(e) { showErr(\'scanStatus\', e); }
}

async function deleteSelectedScan() {
  if (!selectedScanId) return statusEl(\'scanStatus\', \'No scan selected.\', \'err\');
  const scan = scans.find(s => s.scan_id === selectedScanId);
  const label = scan ? `${scan.survey_id} · ${scan.candidate_count} fields · ${scan.scan_id.slice(-8)}` : selectedScanId;
  if (!confirm(`Delete scan "${label}" and all its ${scan?.candidate_count || \'\'} candidates? This cannot be undone.`)) return;
  try {
    await apiFetch(`/pdf-scans/${selectedScanId}`, {method: \'DELETE\'});
    selectedScanId = \'\';
    candidates = [];
    candidateSections = [];
    renderCandidates();
    statusEl(\'scanStatus\', `Scan deleted.`, \'ok\');
    await loadScans();
  } catch(e) { showErr(\'scanStatus\', e); }
}

async function createScan() {
  statusEl(\'scanStatus\', \'Scanning PDF - this may take a minute...\', \'info\');
  const enrichLim = parseInt($(\'labelEnrichLimit\').value, 10);
  const payload = {
    file_path: $(\'filePath\').value.trim(),
    survey_id: $(\'surveyId\').value.trim() || \'uploaded_pdf\',
    require_label_enrichment: true,
    allow_enrichment_fallback: false,
    label_enrichment_candidate_limit: Number.isFinite(enrichLim) && enrichLim>0 ? enrichLim : null,
  };
  try {
    const scan = await apiFetch(\'/pdf-scans\', {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'}, body:JSON.stringify(payload)
    });
    selectedScanId = scan.scan_id;
    await loadScans();
    await selectScan(scan.scan_id);
    statusEl(\'scanStatus\', `Scan ready: ${scan.candidate_count} field(s) found.`, \'ok\');
    dump(scan);
  } catch(e) { showErr(\'scanStatus\', e); }
}

async function selectScan(scanId) {
  if (!scanId) return;
  selectedScanId = scanId;
  $(\'scanSelect\').value = scanId;
  await loadSelectedScan();
  await loadResolvedValues(false);
}

let candidateSections = [];

async function loadSelectedScan() {
  if (!selectedScanId) return statusEl(\'reviewStatus\', \'Choose a scan first.\', \'err\');
  try {
    const data = await apiFetch(`/pdf-scans/${selectedScanId}/candidates/by-section`);
    candidateSections = data.sections || [];
    candidates = candidateSections.flatMap(s => s.candidates);
    renderCandidates();
    populateSectionDropdown(candidateSections);
    statusEl(\'reviewStatus\', `${data.total_candidates} PDF field(s) across ${candidateSections.length} section(s).`, \'ok\');
  } catch(e) { showErr(\'reviewStatus\', e); }
}

function populateSectionDropdown(sections) {
  const sel = $(\'analystSqlSectionFilter\');
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = \'<option value="">All candidates (not recommended for large scans)</option>\' +
    sections.map(s =>
      `<option value="${escHtml(s.section_id)}">${escHtml(s.section_label)} (${s.candidate_count})</option>`
    ).join(\'\');
  if (prev) sel.value = prev;
}

function renderCandidates() {
  const q = $(\'fieldSearch\').value.trim().toLowerCase();
  const filter = $(\'fieldFilter\').value;
  const container = $(\'candidateSections\');
  if (!candidateSections.length) {
    container.innerHTML = \'<p style="color:var(--text-muted);font-size:13px;padding:8px 0">Select a scan above to see fields grouped by section.</p>\';
    $(\'metricFields\').textContent = \'0\';
    $(\'metricLabeled\').textContent = \'0\';
    return;
  }
  let totalShown = 0, totalLabeled = 0;
  const html = candidateSections.map(section => {
    const filtered = section.candidates.filter(c => {
      const text = `${c.field_name} ${c.label_text} ${c.datapoint_intent||\'\'} ${c.page_number||\'\'}`.toLowerCase();
      const explained = c.label_source === \'openai_enriched\';
      return (!q || text.includes(q)) && (
        filter === \'all\' ||
        (filter === \'explained\' && explained) ||
        (filter === \'needs_label\' && !explained)
      );
    });
    if (!filtered.length) return \'\';
    totalShown += filtered.length;
    totalLabeled += filtered.filter(c => c.label_source === \'openai_enriched\').length;
    const rows = filtered.slice(0, 200).map(c => {
      const src = c.label_source === \'openai_enriched\'
        ? \'<span class="pill pill-good">AI-labeled</span>\'
        : \'<span class="pill pill-warn">raw</span>\';
      const meaning = c.datapoint_intent
        ? `${escHtml(c.label_text)}<br><span class="td-muted">${escHtml(c.datapoint_intent)}</span>`
        : escHtml(c.label_text);
      return `<tr>
        <td class="td-mono">${escHtml(c.field_name||c.candidate_key)}</td>
        <td>${meaning}</td>
        <td>${c.page_number||\'-\'}</td>
        <td>${src}</td>
      </tr>`;
    }).join(\'\');
    const truncNote = filtered.length > 200 ? `<p style="font-size:12px;color:var(--text-muted);padding:4px 0">Showing 200 of ${filtered.length}</p>` : \'\';
    return `<details class="section-accordion" open>
      <summary style="font-weight:600;padding:8px 0;cursor:pointer;user-select:none">
        ${escHtml(section.section_label)}
        <span style="font-weight:400;color:var(--text-muted);font-size:12px;margin-left:6px">${filtered.length} field(s)</span>
      </summary>
      <div class="table-wrap" style="margin:4px 0 12px">
        <table>
          <thead><tr><th>Field</th><th>Plain-English meaning</th><th>Pg</th><th>Source</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${truncNote}
      </div>
    </details>`;
  }).join(\'\');
  container.innerHTML = html || \'<p style="color:var(--text-muted);font-size:13px;padding:8px 0">No fields match this filter.</p>\';
  $(\'metricFields\').textContent = String(candidates.length);
  $(\'metricLabeled\').textContent = String(totalLabeled);
}

async function resolveViaGenie() {
  if (!selectedScanId) return statusEl(\'genieStatus\',\'No scan selected\',\'err\');
  const year = parseInt($(\'genieYear\').value,10)||2024;
  const batch = parseInt($(\'genieBatchSize\').value,10)||15;
  const conf  = parseInt($(\'genieMinConf\').value,10)||60;
  const force = $(\'genieForce\').value === \'true\';
  statusEl(\'genieStatus\',\'Sending fields to Genie - this takes several minutes...\',\'info\');
  try {
    const r = await apiFetch(`/pdf-scans/${selectedScanId}/resolve-via-genie`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'},
      body: JSON.stringify({survey_year:year, batch_size:batch, min_confidence:conf, force_regenie:force}),
    });
    statusEl(\'genieStatus\',
      `Resolved: ${r.resolved}  Low-conf: ${r.low_confidence}  Skipped: ${r.skipped}  Failed: ${r.failed}`, \'ok\');
    dump(r);
    await loadResolvedValues(false);
  } catch(e) { showErr(\'genieStatus\', e); }
}

async function resolveDirect() {
  if (!selectedScanId) return statusEl(\'genieStatus\',\'No scan selected\',\'err\');
  const year = parseInt($(\'genieYear\').value,10)||2024;
  statusEl(\'genieStatus\',\'Re-running stored SQL against Databricks...\',\'info\');
  try {
    const r = await apiFetch(`/pdf-scans/${selectedScanId}/resolve-direct`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'},
      body: JSON.stringify({survey_year:year}),
    });
    statusEl(\'genieStatus\',
      `Refreshed: ${r.refreshed}  Null: ${r.null_results}  Errors: ${r.sql_errors}  Needs re-Genie: ${r.needs_regenie}`, \'ok\');
    dump(r);
    await loadResolvedValues(false);
  } catch(e) { showErr(\'genieStatus\', e); }
}

async function loadResolvedValues(showStatus=true) {
  if (!selectedScanId) return statusEl(\'reviewStatus\',\'No scan selected\',\'err\');
  if (showStatus) statusEl(\'reviewStatus\',\'Loading resolved values...\',\'info\');
  try {
    resolvedValues = await apiFetch(`/pdf-scans/${selectedScanId}/resolved-values`);
    renderResolvedValues();
    $(\'metricResolved\').textContent = String(resolvedValues.filter(r=>r.status===\'GENIE_RESOLVED\').length);
    if (showStatus) statusEl(\'reviewStatus\',`${resolvedValues.length} row(s) loaded.`,\'ok\');
  } catch(e) { showErr(\'reviewStatus\', e); }
}

function renderResolvedValues() {
  $(\'resolvedRows\').innerHTML = resolvedValues.length ? resolvedValues.map(r => {
    const cls = r.status===\'GENIE_RESOLVED\' ? \'pill-good\'
              : r.status===\'GENIE_LOW_CONFIDENCE\' ? \'pill-warn\' : \'pill-bad\';
    const statusLabel = r.status.replace(\'GENIE_\',\'\').replace(\'_\',\' \');
    return `<tr>
      <td class="td-mono">${escHtml(r.field_name)}</td>
      <td><strong>${escHtml(r.genie_value||\'\u2014\')}</strong><br><span class="td-muted">${escHtml(r.label_text)}</span></td>
      <td><span class="pill ${cls}">${statusLabel}</span><br><span class="td-muted">${r.genie_confidence||0}%</span></td>
      <td>
        <span class="td-muted">${escHtml(r.genie_reason||\'\')} </span>
        ${r.genie_sql_template ? `<details class="sql"><summary>View SQL</summary><pre>${escHtml(r.genie_sql_template)}</pre></details>` : \'\'}
      </td>
    </tr>`;
  }).join(\'\') : \'<tr class="empty-row"><td colspan="4">No resolved values stored for this scan yet.</td></tr>\';
}

async function previewAnalystSql() {
  if (!selectedScanId) return statusEl(\'analystSqlStatus\',\'No scan selected\',\'err\');
  const year = parseInt($(\'genieYear\').value,10)||2025;
  const rowLimit = parseInt($(\'analystSqlLimit\').value,10)||100;
  const sqlText = $(\'analystSqlText\').value.trim();
  if (!sqlText) return statusEl(\'analystSqlStatus\',\'Paste SQL first.\',\'err\');
  statusEl(\'analystSqlStatus\',\'Running SQL preview in Databricks...\',\'info\');
  try {
    const r = await apiFetch(`/pdf-scans/${selectedScanId}/analyst-sql/preview`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'},
      body: JSON.stringify({
        name: $(\'analystSqlName\').value.trim() || \'Analyst SQL mapping\',
        sql_text: sqlText,
        survey_year: year,
        row_limit: rowLimit,
      }),
    });
    analystSqlQueryId = r.query_id;
    analystSqlDrafts = [];
    $(\'metricSqlRows\').textContent = String(r.row_count||0);
    $(\'metricSqlDrafts\').textContent = \'0\';
    renderAnalystSqlDrafts();
    statusEl(\'analystSqlStatus\',`Preview ready: ${r.row_count} row(s), ${r.columns.length} column(s).`,\'ok\');
    dump(r);
  } catch(e) { showErr(\'analystSqlStatus\', e); }
}

async function autoMapAnalystSql() {
  if (!analystSqlQueryId) return statusEl(\'analystSqlStatus\',\'Preview SQL before auto-mapping.\',\'err\');
  statusEl(\'analystSqlStatus\',\'Asking Databricks Serving Sonnet to propose mappings...\',\'info\');
  try {
    const sectionFilter = ($(\'analystSqlSectionFilter\')?.value || \'\').trim();
    const r = await apiFetch(`/analyst-sql/${analystSqlQueryId}/auto-map`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'},
      body: JSON.stringify({max_drafts:50, section_filter: sectionFilter}),
    });
    analystSqlDrafts = r.drafts || [];
    $(\'metricSqlDrafts\').textContent = String(analystSqlDrafts.filter(d=>d.status===\'PENDING_APPROVAL\').length);
    renderAnalystSqlDrafts();
    statusEl(\'analystSqlStatus\',`${r.drafted_count} mapping proposal(s) ready for review.`,\'ok\');
    dump(r);
  } catch(e) { showErr(\'analystSqlStatus\', e); }
}

function renderAnalystSqlDrafts() {
  const rows = analystSqlDrafts;
  $(\'analystSqlRows\').innerHTML = rows.length ? rows.map(d => {
    const status = d.status === \'APPROVED\' ? \'<span class="pill pill-good">Approved</span>\' : \'<span class="pill pill-warn">Ready for review</span>\';
    const action = d.status === \'APPROVED\'
      ? \'<span class="td-muted">Saved for rerun</span>\'
      : `<button class="btn btn-secondary" onclick="approveAnalystSqlDraft(\'${escHtml(d.draft_id)}\')">Approve</button>`;
    return `<tr>
      <td class="td-mono">${escHtml(d.field_name)}<br><span class="td-muted">${escHtml(d.label_text||\'\')}</span></td>
      <td>Row ${d.source_row_index + 1}, column <span class="td-mono">${escHtml(d.source_column)}</span><br><span class="td-muted">${escHtml(d.reason||\'\')}</span></td>
      <td><strong>${escHtml(d.value_preview||\'\')}</strong></td>
      <td>${status}<br><span class="td-muted">${d.confidence||0}%</span></td>
      <td>${action}</td>
    </tr>`;
  }).join(\'\') : \'<tr class="empty-row"><td colspan="5">Preview SQL, then click Auto-map with Sonnet.</td></tr>\';
}

async function approveAnalystSqlDraft(draftId) {
  statusEl(\'analystSqlStatus\',\'Approving mapping and writing value to this PDF field...\',\'info\');
  try {
    const r = await apiFetch(`/analyst-sql-mapping-drafts/${draftId}/approve`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'}, body:JSON.stringify({}),
    });
    analystSqlApprovedCount += 1;
    $(\'metricSqlApproved\').textContent = String(analystSqlApprovedCount);
    analystSqlDrafts = analystSqlDrafts.map(d => d.draft_id===draftId ? {...d, status:\'APPROVED\', value_preview:r.value} : d);
    $(\'metricSqlDrafts\').textContent = String(analystSqlDrafts.filter(d=>d.status===\'PENDING_APPROVAL\').length);
    renderAnalystSqlDrafts();
    await loadResolvedValues(false);
    statusEl(\'analystSqlStatus\',`Approved ${r.field_name}; value ${r.value} is ready for review/export.`,\'ok\');
    dump(r);
  } catch(e) { showErr(\'analystSqlStatus\', e); }
}

async function rerunApprovedSql() {
  if (!analystSqlQueryId) return statusEl(\'analystSqlStatus\',\'Preview or select an analyst SQL mapping first.\',\'err\');
  const year = parseInt($(\'genieYear\').value,10)||2025;
  statusEl(\'analystSqlStatus\',\'Rerunning approved SQL mappings...\',\'info\');
  try {
    const r = await apiFetch(`/analyst-sql/${analystSqlQueryId}/rerun-approved`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'}, body:JSON.stringify({survey_year:year}),
    });
    await loadResolvedValues(false);
    statusEl(\'analystSqlStatus\',`Rerun complete: ${r.refreshed} refreshed, ${r.missing} missing, ${r.sql_errors} SQL error(s).`,\'ok\');
    dump(r);
  } catch(e) { showErr(\'analystSqlStatus\', e); }
}

async function exportFilledPdf() {
  if (!selectedScanId) return statusEl(\'pdfExportStatus\',\'No scan selected\',\'err\');
  statusEl(\'pdfExportStatus\',\'Creating filled PDF...\',\'info\');
  $(\'pdfExportLink\').innerHTML = \'\';
  try {
    const result = await apiFetch(`/pdf-scans/${selectedScanId}/export-filled-pdf`, {
      method:\'POST\', headers:{\'Content-Type\':\'application/json\'}, body:JSON.stringify({flatten:false})
    });
    statusEl(\'pdfExportStatus\',`Filled ${result.filled_count} field(s) - Skipped ${result.skipped_count}`,\'ok\');
    $(\'pdfExportLink\').innerHTML = result.download_url
      ? `<a href="${escHtml(result.download_url)}" target="_blank" rel="noopener" style="color:var(--accent-2);font-weight:700">Download filled PDF</a>`
      : `<span class="td-muted">${escHtml(result.output_file_path)}</span>`;
    dump(result);
  } catch(e) { showErr(\'pdfExportStatus\', e); }
}

loadScans().catch(e => statusEl(\'scanStatus\', e.message, \'err\'));
</script>
</body>
</html>"""




@app.get("/data-points/fill-preview", response_model=FillPreviewResponse)
def data_points_fill_preview(
    scan_id: str | None = None,
    session: Session = Depends(get_session),
) -> FillPreviewResponse:
    data_path = _fake_form_input_data_path(_fake_form_data_path())
    return build_fill_preview(session, scan_id=scan_id, web_payload_path=data_path)

@app.get("/data-points", response_class=HTMLResponse)
def data_points_page() -> str:
    return data_points_page_html(_SHARED_CSS, _SHARED_JS + fill_preview_js(), _nav("/data-points"))


@app.get("/pdf-vision-ops", response_class=HTMLResponse)
def pdf_vision_ops_page() -> str:
    return pdf_vision_ops_page_html(_SHARED_CSS, _SHARED_JS, _nav("/pdf-vision-ops"))


@app.get("/website-automation", response_class=HTMLResponse)
def website_automation_page() -> str:
    return website_automation_page_html(_SHARED_CSS, _SHARED_JS, _nav("/website-automation"))


@app.get("/website-ops", response_class=HTMLResponse)
@app.get("/ops", response_class=HTMLResponse)
def website_ops_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Website Survey Operations - Survey Automation</title>
  <meta name="description" content="Pull Databricks data and fill a live survey website using Skyvern AI browser automation." />
""" + _SHARED_CSS + """
</head>
<body>
""" + _nav("/website-ops") + """
<div class="page">
  <div class="page-header">
    <h1>Website Survey Operations</h1>
    <p><strong>Fast Skyvern fill:</strong> Website scan and form fill using your attached browser session. Review fill values on the right, connect your browser on the left, then run. Shared catalog: <a href="/data-points">Data Points</a>.</p>
  </div>

  <div class="two-col">
    <!-- LEFT: Launch -->
    <div class="stack">



      <!-- CDP browser connection -->
      <div class="card">
        <div class="card-header" style="margin-bottom:12px">
          <div class="step-badge">1</div>
          <div class="card-header-text">
            <h2>Connect browser (CDP)</h2>
            <p>Attach Skyvern to your current Chrome or Edge window. Log into the survey site in that browser first when authentication is required.</p>
          </div>
        </div>
        <div class="info-panel" style="margin-bottom:12px">
          <div><strong>Current mode:</strong> <span id="browserModeLabel">loading…</span></div>
          <div class="td-muted" style="margin-top:4px"><strong>CDP URL:</strong> <code id="browserCdpUrl">—</code></div>
        </div>
        <details class="accordion">
          <summary>Launch commands &amp; connection check</summary>
          <div class="accordion-body">
            <div class="field span2"><label class="field-label">Edge launch command</label><div class="inline-edit"><input id="edgeLaunchCmd" readonly /><button type="button" class="btn btn-secondary" onclick="copyText('edgeLaunchCmd')">Copy</button></div></div>
            <div class="field span2" style="margin-top:10px"><label class="field-label">Chrome launch command</label><div class="inline-edit"><input id="chromeLaunchCmd" readonly /><button type="button" class="btn btn-secondary" onclick="copyText('chromeLaunchCmd')">Copy</button></div></div>
            <div class="btn-row"><button type="button" class="btn btn-secondary" onclick="checkBrowserConnection()">Check browser connection</button></div>
            <div id="browserCheckStatus" class="status-line"></div>
          </div>
        </details>
        <div class="checkbox-row" style="margin-top:12px">
          <input type="checkbox" id="useCurrentBrowser" onchange="syncBrowserSession()" />
          <label for="useCurrentBrowser">Use current browser session</label>
        </div>
        <div class="field" style="margin-top:10px">
          <label class="field-label">Browser session ID</label>
          <input id="browserSessionId" placeholder="sess_usnews_2025 (optional)" />
        </div>
      </div>

      <!-- Launch card -->
      <div class="card">
        <div class="card-header" style="margin-bottom:14px">
          <div class="step-badge">2</div>
          <div class="card-header-text">
            <h2>Run web fill</h2>
            <p>Pulls fresh Databricks data, then sends one Skyvern task to fill the survey form.</p>
          </div>
        </div>

        <div class="field-grid">
          <div class="field span2">
            <label class="field-label">Website form URL
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">The survey website Skyvern should fill. If already open in your attached browser, use the same URL.</span>
              </span>
            </label>
            <input id="portalUrl" value="http://fake-form/?realData=1" />
          </div>
          <div class="field">
            <label class="field-label">Survey year
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">Leave blank to use the latest Fall term. Use e.g. 2025 to query a specific year.</span>
              </span>
            </label>
            <input id="surveyYear" placeholder="e.g. 2025 (optional)" />
          </div>
          <div class="field">
            <label class="field-label">Max wait time (seconds)
              <span class="tip-icon" tabindex="0">?
                <span class="tip-text">Total timeout before the job is marked failed. Use 1800+ for full surveys.</span>
              </span>
            </label>
            <input id="timeoutSeconds" value="1800" type="number" />
          </div>
        </div>

        <details class="accordion" style="margin-top:12px">
          <summary>Login &amp; advanced options</summary>
          <div class="accordion-body">
            <div class="checkbox-row"><input type="checkbox" id="validateData" checked /><label for="validateData">Validate Databricks data before filling</label></div>
            <div class="field" style="margin-top:10px"><label class="field-label">Skyvern max steps</label><input id="skyvernMaxSteps" value="80" type="number" min="20" max="300" /></div>
            <div class="checkbox-row" style="margin-top:12px"><input type="checkbox" id="needsLogin" onchange="toggleLoginFields()" /><label for="needsLogin">Website requires login</label></div>
            <div id="loginFields" style="display:none;margin-top:10px">
              <div class="field-grid">
                <div class="field"><label class="field-label">Login username</label><input id="loginUsername" autocomplete="off" /></div>
                <div class="field"><label class="field-label">Login password</label><input id="loginPassword" type="password" autocomplete="off" /></div>
              </div>
              <div class="info-panel" style="margin-top:10px"><strong>No passwords are stored by this page.</strong> Credentials are used only for the running Skyvern task.</div>
            </div>
          </div>
        </details>

        <div id="wsBanner" class="banner" style="margin-top:14px"></div>

        <div id="wsProgressWrap" style="display:none;margin:14px 0">
          <div class="stepper" id="wsStepper"></div>
        </div>

        <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
          <button class="btn btn-launch" id="launchBtn" onclick="startJob()">
            <span class="spinner" id="spinner" style="display:none"></span>
            <span id="launchLabel">Run Website Automation</span>
          </button>
        </div>

        <div id="wsLog" style="display:none;margin-top:14px">
          <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Live log</div>
          <div class="log-box" id="logBox"></div>
        </div>
      </div>

    </div>

    <!-- RIGHT: Review + history -->
    <div class="stack">
      <div class="card">
        <div class="card-header">
          <div class="step-badge">3</div>
          <div class="card-header-text">
            <h2>Review web fill values</h2>
            <p>Values Skyvern will type into the live form after the Databricks pull step.</p>
          </div>
        </div>
        <div class="stat-row">
          <div class="stat"><strong id="wpStatWebReady">—</strong><span>Web ready</span></div>
          <div class="stat"><strong id="wpStatBothReady">—</strong><span>Also in PDF</span></div>
          <div class="stat"><strong id="wpStatMissing">—</strong><span>Missing web value</span></div>
          <div class="stat"><a href="/data-points" class="btn btn-ghost" style="margin-top:6px;font-size:12px">Full catalog →</a><span></span></div>
        </div>
        <div class="field" style="margin-bottom:10px"><input id="wpSearch" placeholder="Filter values to fill…" oninput="renderWebPreview()" /></div>
        <div class="table-wrap" style="max-height:420px">
          <table>
            <thead><tr><th>Field</th><th>Meaning</th><th>Web fill value</th><th>Status</th></tr></thead>
            <tbody id="wpPreviewRows"><tr class="empty-row"><td colspan="4">Loading preview…</td></tr></tbody>
          </table>
        </div>
        <div id="wpPreviewStatus" class="status-line"></div>
      </div>
      <details class="accordion">
        <summary>Run history</summary>
        <div class="accordion-body">
          <div class="btn-row" style="margin-top:0"><button class="btn btn-ghost" onclick="refreshHistory()">Refresh</button></div>
          <div class="table-wrap" style="max-height:260px;margin-top:10px">
            <table>
              <thead><tr><th>Time</th><th>Status</th><th>Duration</th><th>URL</th><th></th></tr></thead>
              <tbody id="historyBody"><tr class="empty-row"><td colspan="5">No runs yet.</td></tr></tbody>
            </table>
          </div>
          <div id="jobDetail" style="display:none;margin-top:12px" class="info-panel">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><strong>Job detail</strong><button class="btn btn-ghost" style="font-size:11px;padding:3px 8px" onclick="hideJobDetail()">Close</button></div>
            <div id="jobDetailContent"></div>
          </div>
        </div>
      </details>
    </div>
  </div>
</div>

<script>
""" + _SHARED_JS + fill_preview_js() + """
const STEPS_CONFIG = [
  { key: \'pull_real_data\', label: \'Pull Databricks data\' },
  { key: \'run_full_fill\',  label: \'Fill website form\' },
];
let activeJobId = null, pollTimer = null, fillPreview = null;
const $ = id => document.getElementById(id);

function statusEl(id, text, type=\'ok\') {
  const el = $(id);
  if (!text) { el.className=\'status-line\'; el.textContent=\'\'; return; }
  el.className = `status-line show ${type}`;
  el.textContent = text;
}

function setBanner(type, title, body=\'\') {
  const el = $(\'wsBanner\');
  const icons = { info:\'Info\', ok:\'Done\', error:\'Error\' };
  el.className = `banner show ${type}`;
  el.innerHTML = `<span class="banner-icon">${icons[type]||\'\'}</span>
    <span class="banner-text"><strong>${title}</strong>${body ? \' \' + body : \'\' }</span>`;
}
function clearBanner() { const el=$(\'wsBanner\'); el.className=\'banner\'; el.innerHTML=\'\'; }

function badge(s) { return `<span class="badge badge-${s}">${s}</span>`; }

function elapsed(a, b) {
  if (!a) return \'\';
  const sec = Math.round((new Date(b||Date.now()) - new Date(a)) / 1000);
  return sec < 60 ? `${sec}s` : `${Math.floor(sec/60)}m ${sec%60}s`;
}
function fmtTime(iso) {
  if (!iso) return \'\';
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}

function renderSteps(steps, jobStatus) {
  const wrap = $(\'wsProgressWrap\'), row = $(\'wsStepper\');
  wrap.style.display = \'block\'; row.innerHTML = \'\';
  const completed = new Set((steps||[]).filter(s=>s.status===\'completed\').map(s=>s.name));
  const failed    = new Set((steps||[]).filter(s=>s.status===\'failed\').map(s=>s.name));
  const running   = jobStatus===\'running\' ? STEPS_CONFIG.find(s=>!completed.has(s.key)&&!failed.has(s.key)) : null;
  STEPS_CONFIG.forEach((cfg, i) => {
    const done=completed.has(cfg.key), fail=failed.has(cfg.key), active=running&&running.key===cfg.key;
    const dotCls = done?'done':fail?'failed':active?'active':'';
    const dotNum = done?'v':fail?'x':(i+1).toString();
    row.insertAdjacentHTML('beforeend',
      `<div class="step-dot-wrap">
        <div class="step-dot ${dotCls}">${dotNum}</div>
        <div class="step-label">${cfg.label}</div>
      </div>`);
    if (i < STEPS_CONFIG.length-1) {
      row.insertAdjacentHTML(\'beforeend\',`<div class="step-line ${done?\'done\':\'\'}"></div>`);
    }
  });
}

function renderLog(steps, error) {
  const lines = [];
  (steps||[]).forEach(s => {
    lines.push(`> ${s.name.replace(/_/g,\' \').toUpperCase()} - ${s.status}`);
    if (s.stdout) lines.push(s.stdout.trim().split(\'\\n\').slice(-30).join(\'\\n\'));
    if (s.stderr && s.status===\'failed\') { lines.push(\'-- stderr --\'); lines.push(s.stderr.trim().split(\'\\n\').slice(-10).join(\'\\n\')); }
    lines.push(\'\');
  });
  if (error) { lines.push(\'-- ERROR --\'); lines.push(error); }
  if (lines.length) {
    $(\'wsLog\').style.display=\'block\';
    const lb=$(\'logBox\'); lb.textContent=lines.join(\'\\n\'); lb.scrollTop=lb.scrollHeight;
  }
}

function startPolling() { stopPolling(); pollTimer=setInterval(tick, 3000); }
function stopPolling() { if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }

async function tick() {
  if (!activeJobId) return;
  try {
    const job = await apiFetch(`/website-ops/full-workflow/jobs/${activeJobId}`);
    renderSteps(job.steps, job.status);
    renderLog(job.steps, job.error);
    refreshHistory();
    if (job.status==='completed') {
      stopPolling(); setLaunchIdle();
      setBanner('ok', 'Automation complete', 'The survey form was filled successfully.'); reloadWebPreview();
    } else if (job.status==='failed') {
      stopPolling(); setLaunchIdle();
      setBanner('error', 'Run failed', friendlyError(job.error||''));
    }
  } catch { /* network blip */ }
}

function friendlyError(raw) {
  if (raw.includes(\'FileNotFoundError\')) return \'Data file not found. Check the artifacts volume mount.\';
  if (raw.includes(\'SKYVERN_API_KEY\'))   return \'Skyvern API key is missing in .env.\';
  if (raw.includes(\'9222\'))              return \'Cannot reach the browser. Is Edge/Chrome running with CDP?\';
  if (raw.includes(\'Databricks\'))        return \'Could not connect to Databricks. Check credentials in .env.\';
  if (raw.includes(\'max steps\')||raw.includes(\'planning iterations\'))
                                           return \'Skyvern hit its step limit. Lower SKYVERN_MAX_FIELDS_PER_TASK or increase max steps.\';
  return raw.split(\'\\n\').slice(-2).join(\' \').trim() || \'Unexpected error.\';
}

function setLaunchRunning() {
  $(\'launchBtn\').disabled=true;
  $(\'spinner\').style.display=\'inline-block\';
  $(\'launchLabel\').textContent=\'Running...\';
}
function setLaunchIdle() {
  $(\'launchBtn\').disabled=false;
  $(\'spinner\').style.display=\'none\';
  $(\'launchLabel\').textContent=\'Run Website Automation\';
}


function copyText(id) {
  const el = $(id);
  el.select();
  document.execCommand('copy');
  statusEl('browserCheckStatus', 'Copied to clipboard.', 'ok');
}

function toggleLoginFields() {
  $('loginFields').style.display = $('needsLogin').checked ? 'block' : 'none';
}

function syncBrowserSession() {
  if ($('useCurrentBrowser').checked && !$('browserSessionId').value.trim()) {
    $('browserSessionId').placeholder = 'Auto-generated when you run';
  }
}

async function loadBrowserConfig() {
  try {
    const cfg = await apiFetch('/website-ops/browser-config');
    $('browserModeLabel').textContent = cfg.cdp_connect_enabled ? 'CDP connect (attached browser)' : `${cfg.browser_type} (Skyvern-managed browser)`;
    $('browserCdpUrl').textContent = cfg.cdp_url || 'not configured';
    $('edgeLaunchCmd').value = cfg.edge_launch_command || '';
    $('chromeLaunchCmd').value = cfg.chrome_launch_command || '';
  } catch(e) {
    statusEl('browserCheckStatus', 'Could not load browser config: ' + e.message, 'err');
  }
}

async function checkBrowserConnection() {
  statusEl('browserCheckStatus', 'Checking CDP connection…', 'info');
  try {
    const result = await apiFetch('/website-ops/browser-check');
    statusEl('browserCheckStatus', result.message || (result.connected ? 'Connected' : 'Not connected'), result.connected ? 'ok' : 'err');
  } catch(e) {
    statusEl('browserCheckStatus', 'Check failed: ' + e.message, 'err');
  }
}

async function startJob() {
  clearBanner();
  $(\'logBox\').textContent=\'\'; $(\'wsLog\').style.display=\'none\';
  $(\'wsProgressWrap\').style.display=\'none\';
  const yearRaw = $(\'surveyYear\').value.trim();
  if ($(\'needsLogin\').checked) {
    if (!$(\'loginUsername\').value.trim() || !$(\'loginPassword\').value) {
      setBanner(\'error\', \'Login required\', \'Enter username and password, or uncheck Website requires login.\');
      return;
    }
  }
  const payload = {
    portal_url: $(\'portalUrl\').value.trim()||\'http://fake-form/?realData=1\',
    timeout_seconds: Number($(\'timeoutSeconds\').value.trim()||\'1800\'),
    validate: $(\'validateData\').checked,
    survey_year: yearRaw ? Number(yearRaw) : null,
    browser_session_id: $(\'browserSessionId\').value.trim()||null,
    use_current_browser: $(\'useCurrentBrowser\').checked,
    needs_login: $(\'needsLogin\').checked,
    username: $(\'needsLogin\').checked ? $(\'loginUsername\').value.trim() : null,
    password: $(\'needsLogin\').checked ? $(\'loginPassword\').value : null,
    skyvern_max_steps: Number($(\'skyvernMaxSteps\').value.trim()||\'80\'),
  };
  setLaunchRunning();
  setBanner(\'info\',\'Starting automation...\',\'Pulling Databricks data and queuing the browser fill.\');
  try {
    const res = await fetch(\'/website-ops/full-workflow/jobs\',
      {method:\'POST\', headers:{\'Content-Type\':\'application/json\'}, body:JSON.stringify(payload)});
    const body = await res.json();
    if (!res.ok) { setLaunchIdle(); setBanner(\'error\',\'Could not start run\', body.detail||\'Unknown error\'); return; }
    activeJobId = body.job_id;
    startPolling();
  } catch { setLaunchIdle(); setBanner(\'error\',\'Network error\',\'Could not reach the automation server.\'); }
}

async function refreshHistory() {
  try {
    const jobs = await apiFetch(\'/website-ops/full-workflow/jobs\');
    const tbody = $(\'historyBody\');
    if (!jobs.length) { tbody.innerHTML=\'<tr class="empty-row"><td colspan="5">No runs yet.</td></tr>\'; return; }
    tbody.innerHTML = jobs.map(j => `<tr>
      <td style="white-space:nowrap">${fmtTime(j.started_at)||\' queued\'}</td>
      <td>${badge(j.status)}</td>
      <td>${elapsed(j.started_at, j.finished_at)}</td>
      <td style="font-size:11px;color:var(--muted)">${escHtml((j.portal_url||'').slice(0,50))}</td>
      <td><button class="btn btn-ghost" style="font-size:11px;padding:3px 8px" onclick="viewJob(\'${j.job_id}\')">Detail</button></td>
    </tr>`).join(\'\');
  } catch { /* ignore */ }
}

async function viewJob(jobId) {
  activeJobId = jobId;
  clearBanner();
  try {
    const job = await apiFetch(`/website-ops/full-workflow/jobs/${jobId}`);
    renderSteps(job.steps, job.status);
    renderLog(job.steps, job.error);
    const steps = (job.steps||[]).map(s => `
      <div style="margin-bottom:10px;padding:10px 12px;background:var(--surface-2);border-radius:var(--radius-sm);border:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <strong style="font-size:13px">${escHtml(s.name)}</strong>
          <span class="badge badge-${s.status}">${s.status}</span>
        </div>
        ${s.parsed_output ? `<div style="font-size:12px;color:var(--muted)">${Object.entries(s.parsed_output).map(([k,v])=>`<span style="margin-right:10px">${escHtml(k)}: <strong>${escHtml(String(v))}</strong></span>`).join(\'\')}</div>` : \'\'}
        
        ${s.stderr && s.status==='failed' ? `<details class="sql" style="margin-top:6px"><summary>Error output</summary><pre>${escHtml(s.stderr.trim().slice(-800))}</pre></details>` : ''}
      </div>`);
    $('jobDetailContent').innerHTML = `
      <div style="font-size:12px;color:var(--muted);margin-bottom:10px">Job ID: <code>${escHtml(jobId)}</code> Started: ${fmtTime(job.started_at)||'-'} Elapsed: ${elapsed(job.started_at, job.finished_at)}</div>
      ${steps.join('')}`;
    $('jobDetail').style.display = 'block';
    if (job.status==='completed') setBanner('ok','Run completed',` Finished at ${fmtTime(job.finished_at)}.`);
    if (job.status==='failed') setBanner('error','Run failed',''+friendlyError(job.error||''));
    if (job.status==='running') startPolling();
  } catch(e) { setBanner('error','Could not load job', e.message); }
}

function renderWebPreview() {
  if (!fillPreview) return;
  const q = ($('wpSearch').value||'').trim().toLowerCase();
  const rows = fillPreview.rows.filter(r => r.web_value || r.web_ready).filter(r => !q || `${r.field_key} ${r.label||''} ${r.web_value||''}`.toLowerCase().includes(q));
  const missing = fillPreview.rows.filter(r => !r.web_ready).length;
  $('wpStatWebReady').textContent = String(fillPreview.web_ready_count);
  $('wpStatBothReady').textContent = String(fillPreview.both_ready_count);
  $('wpStatMissing').textContent = String(missing);
  if (!rows.length) {
    $('wpPreviewRows').innerHTML = '<tr class="empty-row"><td colspan="4">No web fill values in the current payload.</td></tr>';
    return;
  }
  $('wpPreviewRows').innerHTML = rows.slice(0, 120).map(row => `<tr>
    <td class="td-mono">${escHtml(row.field_key)}</td>
    <td>${escHtml(row.label || row.intent || '—')}</td>
    <td>${formatFillValue(row.web_value, row.web_ready)}</td>
    <td>${row.pdf_ready ? '<span class="pill pill-good">Also PDF</span>' : '<span class="pill pill-neutral">Web only</span>'}</td>
  </tr>`).join('');
}

async function reloadWebPreview() {
  statusEl('wpPreviewStatus', 'Loading fill preview…', 'info');
  try {
    fillPreview = await fetchFillPreview(null);
    renderWebPreview();
    statusEl('wpPreviewStatus', `${fillPreview.web_ready_count} web value(s) ready`, 'ok');
  } catch(e) { statusEl('wpPreviewStatus', e.message, 'err'); }
}

function hideJobDetail() { $('jobDetail').style.display='none'; }

loadBrowserConfig();
reloadWebPreview();
refreshHistory();
</script>
</body>
</html>"""




@app.get("/website-ops/browser-config", response_model=dict[str, object])
def website_ops_browser_config() -> dict[str, object]:
    config = _browser_runtime_config()
    browser_type = str(config["browser_type"] or "chromium-headful")
    cdp_url = config["cdp_url"]
    return {
        "browser_type": browser_type,
        "cdp_url": cdp_url,
        "cdp_connect_enabled": browser_type == "cdp-connect" and bool(cdp_url),
        "edge_launch_command": (
            'open -na "Microsoft Edge" --args --remote-debugging-port=9222 '
            '--remote-debugging-address=0.0.0.0 --remote-allow-origins=* '
            '--user-data-dir=~/edge-cdp-profile "YOUR_SURVEY_URL"'
        ),
        "chrome_launch_command": (
            'open -na "Google Chrome" --args --remote-debugging-port=9222 '
            '--remote-debugging-address=0.0.0.0 --remote-allow-origins=* '
            '--user-data-dir=~/chrome-cdp-profile "YOUR_SURVEY_URL"'
        ),
        "env_hint": "Set BROWSER_TYPE=cdp-connect and BROWSER_REMOTE_DEBUGGING_URL in .env, then restart Skyvern.",
    }


@app.get("/website-ops/browser-check", response_model=dict[str, object])
def website_ops_browser_check() -> dict[str, object]:
    config = _browser_runtime_config()
    browser_type = str(config["browser_type"] or "chromium-headful")
    cdp_url = (config["cdp_url"] or "").strip()
    if browser_type != "cdp-connect" or not cdp_url:
        return {
            "browser_type": browser_type,
            "cdp_url": cdp_url or None,
            "connected": False,
            "message": (
                "CDP mode is not active. Set BROWSER_TYPE=cdp-connect and "
                "BROWSER_REMOTE_DEBUGGING_URL, then restart Skyvern."
            ),
        }
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        request = urllib.request.Request(version_url, method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "browser_type": browser_type,
            "cdp_url": cdp_url,
            "connected": False,
            "message": f"Cannot reach browser at {cdp_url}: {exc}",
        }
    return {
        "browser_type": browser_type,
        "cdp_url": cdp_url,
        "connected": True,
        "browser": payload.get("Browser"),
        "protocol_version": payload.get("Protocol-Version"),
        "message": "Browser is reachable via CDP.",
    }

@app.post("/website-ops/full-workflow/jobs", response_model=dict[str, object])
@app.post("/ops/full-workflow/jobs", response_model=dict[str, object])
def launch_full_workflow_job(request: FullWorkflowLaunchRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    if not request.portal_url.strip():
        raise HTTPException(status_code=400, detail="portal_url is required")
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    payload = request.model_copy(deep=True)
    if payload.needs_login:
        if not (payload.username or "").strip():
            raise HTTPException(status_code=400, detail="username is required when needs_login is true")
        if not payload.password:
            raise HTTPException(status_code=400, detail="password is required when needs_login is true")
    job = WorkflowJob(
        job_id=job_id,
        status="queued",
        request_json=json.dumps(_workflow_request_for_storage(payload)),
        steps_json="[]",
    )
    session.add(job)
    session.commit()
    thread = threading.Thread(target=_execute_full_workflow_job, args=(job_id, payload), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/website-ops/full-workflow/jobs", response_model=list[dict[str, object]])
@app.get("/ops/full-workflow/jobs", response_model=list[dict[str, object]])
def list_full_workflow_jobs(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    jobs = list(session.execute(
        select(WorkflowJob).order_by(WorkflowJob.created_at.desc())
    ).scalars())
    results: list[dict[str, object]] = []
    for job in jobs:
        req = _json_payload(job.request_json, default={})
        portal_url = req.get("portal_url") if isinstance(req, dict) else None
        results.append(
            {
                "job_id": job.job_id,
                "status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "portal_url": portal_url,
            }
        )
    return results


@app.get("/website-ops/full-workflow/jobs/{job_id}", response_model=dict[str, object])
@app.get("/ops/full-workflow/jobs/{job_id}", response_model=dict[str, object])
def get_full_workflow_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    job = session.get(WorkflowJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "request": (lambda raw: (raw.pop("username", None), raw.pop("password", None), raw)[2])(
            json.loads(job.request_json) if job.request_json else {}
        ),
        "steps": json.loads(job.steps_json),
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": job.error,
    }


@app.post("/runs", response_model=dict[str, object])
def create_run(request: CreateRunRequest, session: Session = Depends(get_session)) -> dict[str, object]:
    service = _build_slice1_service(session)
    run = service.create_run(run_id=request.run_id, survey_id=request.survey_id, survey_year=request.survey_year)
    return {
        "run_id": run.run_id,
        "survey_id": run.survey_id,
        "survey_year": run.survey_year,
    }


@app.post("/runs/{run_id}/dispatch-validate", response_model=DispatchValidateResponse)
def dispatch_validate_task(
    run_id: str,
    request: DispatchValidateRequest,
    session: Session = Depends(get_session),
) -> DispatchValidateResponse:
    service = _build_slice1_service(session)
    try:
        tasks = service.dispatch_section_validate_activity(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DispatchValidateResponse(
        run_id=run_id,
        task_ids=[task.task_id for task in tasks],
        workflow_ids=[task.workflow_id for task in tasks],
        chunk_total=len(tasks),
    )


@app.post("/runs/{run_id}/scan-fields", response_model=DispatchScanFieldsResponse)
def dispatch_scan_fields_task(
    run_id: str,
    request: DispatchScanFieldsRequest,
    session: Session = Depends(get_session),
) -> DispatchScanFieldsResponse:
    service = _build_slice1_service(session)
    try:
        task = service.dispatch_scan_fields_activity(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DispatchScanFieldsResponse(
        run_id=run_id,
        task_id=task.task_id,
        workflow_id=task.workflow_id,
        status=task.status,
    )


@app.post("/runs/{run_id}/dispatch-fill", response_model=DispatchFillResponse)
def dispatch_fill_task(
    run_id: str,
    request: DispatchFillRequest,
    session: Session = Depends(get_session),
) -> DispatchFillResponse:
    service = _build_slice1_service(session)
    try:
        tasks = service.dispatch_section_fill_activity(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
            scan_id=request.scan_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DispatchFillResponse(
        run_id=run_id,
        task_ids=[task.task_id for task in tasks],
        workflow_ids=[task.workflow_id for task in tasks],
        chunk_total=len(tasks),
        submit_enabled=False,
    )


@app.post("/runs/{run_id}/prepare-section-payload", response_model=PrepareSectionPayloadResponse)
def prepare_section_payload(
    run_id: str,
    request: PrepareSectionPayloadRequest,
    session: Session = Depends(get_session),
) -> PrepareSectionPayloadResponse:
    service = _build_slice1_service(session)
    try:
        run_record = session.get(Run, run_id)
        if not run_record:
            raise ValueError(f"Unknown run_id: {run_id}")
        payload = service.prepare_section_payload_activity(
            run_id=run_id,
            section_id=request.section_id,
            survey_year=run_record.survey_year,
            create_missing_reviews=request.create_missing_reviews,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PrepareSectionPayloadResponse(
        run_id=run_id,
        section_id=request.section_id,
        resolved_values=payload.values,
        missing_fields=payload.missing_fields,
    )


@app.post("/runs/{run_id}/execute-section-pipeline", response_model=ExecuteSectionPipelineResponse)
def execute_section_pipeline(
    run_id: str,
    request: ExecuteSectionPipelineRequest,
    session: Session = Depends(get_session),
) -> ExecuteSectionPipelineResponse:
    service = _build_slice1_service(session)
    try:
        result = service.execute_section_pipeline(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExecuteSectionPipelineResponse(
        run_id=run_id,
        section_id=request.section_id,
        scan_task_id=str(result["scan_task_id"]),
        validate_task_ids=[str(value) for value in result["validate_task_ids"]],
        resolved_field_count=int(result["resolved_field_count"]),
    )


@app.post("/runs/{run_id}/execute-draft-fill-pipeline", response_model=ExecuteDraftFillPipelineResponse)
def execute_draft_fill_pipeline(
    run_id: str,
    request: ExecuteDraftFillPipelineRequest,
    session: Session = Depends(get_session),
) -> ExecuteDraftFillPipelineResponse:
    service = _build_slice1_service(session)
    try:
        result = service.execute_draft_fill_pipeline(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
            scan_id=request.scan_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExecuteDraftFillPipelineResponse(
        run_id=run_id,
        section_id=request.section_id,
        fill_task_ids=[str(task_id) for task_id in result["fill_task_ids"]],
        fill_workflow_ids=[str(workflow_id) for workflow_id in result["fill_workflow_ids"]],
        field_count=int(result["field_count"]),
        submit_enabled=bool(result["submit_enabled"]),
    )


@app.post("/webhooks/skyvern", response_model=SkyvernWebhookResponse)
async def skyvern_webhook(request: Request, session: Session = Depends(get_session)) -> SkyvernWebhookResponse:
    settings = get_settings()
    raw_body = await request.body()
    if settings.skyvern_webhook_secret:
        incoming_signature = request.headers.get("x-skyvern-signature", "")
        expected_signature = hmac.new(
            settings.skyvern_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(incoming_signature, expected_signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    service = _build_slice1_service(session)
    try:
        task, mismatch_count = service.process_skyvern_webhook(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if settings.temporal_signal_enabled:
        signaler = TemporalSignaler(
            target_host=settings.temporal_target_host,
            namespace=settings.temporal_namespace,
        )
        try:
            await signaler.signal_skyvern_webhook(run_id=task.run_id, payload=payload)
            service.record_event(
                task.run_id,
                "TEMPORAL_SIGNAL_SENT",
                {"workflow_id": task.workflow_id, "signal": "skyvern_webhook_received"},
            )
        except Exception as exc:  # noqa: BLE001
            service.record_event(
                task.run_id,
                "TEMPORAL_SIGNAL_FAILED",
                {"workflow_id": task.workflow_id, "error": str(exc)},
            )

    return SkyvernWebhookResponse(
        ok=True,
        run_id=task.run_id,
        workflow_id=task.workflow_id,
        mismatches_created=mismatch_count,
    )


@app.get("/runs/{run_id}/review-items", response_model=list[ReviewItemResponse])
def list_review_items(run_id: str, session: Session = Depends(get_session)) -> list[ReviewItemResponse]:
    service = _build_slice1_service(session)
    items = service.list_review_items(run_id)
    return [
        ReviewItemResponse(
            review_item_id=item.review_item_id,
            field_id=item.field_id,
            reason_code=item.reason_code,
            expected_value=item.expected_value,
            observed_value=item.observed_value,
            screenshot_url=item.screenshot_url,
            status=item.status,
        )
        for item in items
    ]


@app.get("/runs/{run_id}/events", response_model=list[RunEventResponse])
def list_run_events(run_id: str, session: Session = Depends(get_session)) -> list[RunEventResponse]:
    service = _build_slice1_service(session)
    events = service.list_run_events(run_id)
    return [
        RunEventResponse(
            event_type=event.event_type,
            payload_json=event.payload_json,
            created_at=event.created_at.isoformat(),
        )
        for event in events
    ]


@app.get("/runs/{run_id}/field-discovery-drafts", response_model=list[FieldDiscoveryDraftResponse])
def list_field_discovery_drafts(
    run_id: str,
    section_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[FieldDiscoveryDraftResponse]:
    service = _build_slice1_service(session)
    drafts = service.list_field_discovery_drafts(run_id=run_id, section_id=section_id)
    return [
        FieldDiscoveryDraftResponse(
            draft_id=draft.draft_id,
            section_id=draft.section_id,
            label_text=draft.label_text,
            input_kind=draft.input_kind,
            required_flag=draft.required_flag,
            candidate_field_id=draft.candidate_field_id,
            status=draft.status,
            notes=draft.notes,
            screenshot_url=draft.screenshot_url,
        )
        for draft in drafts
    ]


@app.post("/field-discovery-drafts/{draft_id}/approve", response_model=CatalogFieldResponse)
def approve_discovery_draft(
    draft_id: str,
    request: ApproveFieldDiscoveryRequest,
    session: Session = Depends(get_session),
) -> CatalogFieldResponse:
    service = _build_slice1_service(session)
    try:
        catalog = service.approve_field_discovery_draft(
            draft_id=draft_id,
            field_id_override=request.field_id_override,
            databricks_view=request.databricks_view,
            databricks_value_column=request.databricks_value_column,
            databricks_year_column=request.databricks_year_column,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CatalogFieldResponse(
        field_id=catalog.field_id,
        section_id=catalog.section_id,
        label_text=catalog.label_text,
        input_kind=catalog.input_kind,
        required_flag=catalog.required_flag,
        databricks_view=catalog.databricks_view,
        databricks_value_column=catalog.databricks_value_column,
        databricks_year_column=catalog.databricks_year_column,
        transform_json=catalog.transform_json,
        status=catalog.status,
    )


@app.post("/field-discovery-drafts/{draft_id}/reject", response_model=FieldDiscoveryDraftResponse)
def reject_discovery_draft(
    draft_id: str,
    request: RejectFieldDiscoveryRequest,
    session: Session = Depends(get_session),
) -> FieldDiscoveryDraftResponse:
    service = _build_slice1_service(session)
    try:
        draft = service.reject_field_discovery_draft(draft_id=draft_id, notes=request.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FieldDiscoveryDraftResponse(
        draft_id=draft.draft_id,
        section_id=draft.section_id,
        label_text=draft.label_text,
        input_kind=draft.input_kind,
        required_flag=draft.required_flag,
        candidate_field_id=draft.candidate_field_id,
        status=draft.status,
        notes=draft.notes,
        screenshot_url=draft.screenshot_url,
    )


@app.get("/field-catalog/{section_id}", response_model=list[CatalogFieldResponse])
def list_field_catalog(section_id: str, session: Session = Depends(get_session)) -> list[CatalogFieldResponse]:
    service = _build_slice1_service(session)
    rows = service.list_field_catalog(section_id=section_id)
    return [
        CatalogFieldResponse(
            field_id=row.field_id,
            section_id=row.section_id,
            label_text=row.label_text,
            input_kind=row.input_kind,
            required_flag=row.required_flag,
            databricks_view=row.databricks_view,
            databricks_value_column=row.databricks_value_column,
            databricks_year_column=row.databricks_year_column,
            transform_json=row.transform_json,
            status=row.status,
        )
        for row in rows
    ]


@app.patch("/field-catalog/{field_id}/binding", response_model=CatalogFieldResponse)
def update_field_catalog_binding(
    field_id: str,
    request: UpdateCatalogBindingRequest,
    session: Session = Depends(get_session),
) -> CatalogFieldResponse:
    service = _build_slice1_service(session)
    try:
        row = service.update_field_catalog_binding(
            field_id=field_id,
            databricks_view=request.databricks_view,
            databricks_value_column=request.databricks_value_column,
            databricks_year_column=request.databricks_year_column,
            transform_json=request.transform_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CatalogFieldResponse(
        field_id=row.field_id,
        section_id=row.section_id,
        label_text=row.label_text,
        input_kind=row.input_kind,
        required_flag=row.required_flag,
        databricks_view=row.databricks_view,
        databricks_value_column=row.databricks_value_column,
        databricks_year_column=row.databricks_year_column,
        transform_json=row.transform_json,
        status=row.status,
    )


@app.post("/field-catalog/{section_id}/bootstrap", response_model=list[CatalogFieldResponse])
def bootstrap_field_catalog(section_id: str, session: Session = Depends(get_session)) -> list[CatalogFieldResponse]:
    service = _build_slice1_service(session)
    rows = service.bootstrap_section_catalog(section_id)
    return [
        CatalogFieldResponse(
            field_id=row.field_id,
            section_id=row.section_id,
            label_text=row.label_text,
            input_kind=row.input_kind,
            required_flag=row.required_flag,
            databricks_view=row.databricks_view,
            databricks_value_column=row.databricks_value_column,
            databricks_year_column=row.databricks_year_column,
            transform_json=row.transform_json,
            status=row.status,
        )
        for row in rows
    ]


@app.get("/runs/{run_id}/metrics", response_model=RunMetricsResponse)
def get_run_metrics(run_id: str, session: Session = Depends(get_session)) -> RunMetricsResponse:
    service = _build_slice1_service(session)
    metrics = service.compute_run_metrics(run_id=run_id)
    return RunMetricsResponse(**metrics)


@app.post("/runs/{run_id}/start-workflow", response_model=StartWorkflowResponse)
async def start_temporal_workflow(
    run_id: str,
    request: StartWorkflowRequest,
    session: Session = Depends(get_session),
) -> StartWorkflowResponse:
    settings = get_settings()
    run_record = session.get(Run, run_id)
    if not run_record:
        raise HTTPException(status_code=400, detail=f"Unknown run_id: {run_id}")
    signaler = TemporalSignaler(target_host=settings.temporal_target_host, namespace=settings.temporal_namespace)
    try:
        await signaler.start_run_workflow(
            run_id=run_id,
            section_id=request.section_id,
            portal_url=request.portal_url,
            callback_timeout_seconds=request.callback_timeout_seconds,
            workflow_mode=request.workflow_mode,
            browser_session_id=request.browser_session_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to start workflow: {exc}") from exc
    service = _build_slice1_service(session)
    service.record_event(
        run_id,
        "TEMPORAL_WORKFLOW_STARTED",
        {
            "section_id": request.section_id,
            "portal_url": request.portal_url,
            "callback_timeout_seconds": request.callback_timeout_seconds,
            "workflow_mode": request.workflow_mode,
            "browser_session_id": request.browser_session_id,
        },
    )
    return StartWorkflowResponse(run_id=run_id, section_id=request.section_id, started=True)


@app.get("/review/{run_id}", response_class=HTMLResponse)
def review_page(run_id: str, session: Session = Depends(get_session)) -> str:
    service = _build_slice1_service(session)
    review_items = service.list_review_items(run_id)
    events = service.list_run_events(run_id)
    drafts = service.list_field_discovery_drafts(run_id=run_id)

    item_rows = "".join(
        (
            f"<tr><td class='td-mono'>{item.field_id}</td>"
            f"<td><strong>{item.expected_value}</strong></td>"
            f"<td><strong>{item.observed_value}</strong></td>"
            f"<td><span class='pill pill-neutral'>{item.reason_code}</span></td>"
            f"<td><span class='pill {'pill-good' if item.status == 'APPROVED' else 'pill-warn'}'>{item.status}</span></td></tr>"
        )
        for item in review_items
    )
    if not item_rows:
        item_rows = "<tr><td colspan='5' class='empty-row'>No review items yet.</td></tr>"

    event_rows = "".join(
        f"<tr><td>{event.created_at.isoformat()}</td>"
        f"<td><span class='pill pill-accent'>{event.event_type}</span></td>"
        f"<td><pre>{event.payload_json}</pre></td></tr>"
        for event in events
    )
    if not event_rows:
        event_rows = "<tr><td colspan='3' class='empty-row'>No events yet.</td></tr>"

    draft_rows = "".join(
        f"<tr><td>{draft.label_text}</td>"
        f"<td class='td-mono'>{draft.candidate_field_id}</td>"
        f"<td>{draft.input_kind}</td>"
        f"<td><span class='pill {'pill-good' if draft.status == 'APPROVED' else 'pill-warn'}'>{draft.status}</span></td></tr>"
        for draft in drafts
    )
    if not draft_rows:
        draft_rows = "<tr><td colspan='4' class='empty-row'>No field discovery drafts yet.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Run Review - {run_id} — Survey Automation</title>
  {_SHARED_CSS}
</head>
<body>
  {_nav("/website-ops")}
  <div class="page">
    <div class="page-header">
      <h1>Run Review: {run_id}</h1>
      <p>Slice 1 review interface for checking mismatches, system events, and discovered field drafts.</p>
    </div>
    
    <div class="stack">
      <!-- Review Items -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">!</div>
          <div class="card-header-text">
            <h2>Review Items</h2>
            <p>Mismatches and values requiring manual review</p>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Field</th><th>Expected</th><th>Observed</th><th>Reason</th><th>Status</th></tr>
            </thead>
            <tbody>
              {item_rows}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Field Discovery Drafts -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">?</div>
          <div class="card-header-text">
            <h2>Field Discovery Drafts</h2>
            <p>Discovered new form fields awaiting promotion to catalog</p>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Label</th><th>Candidate Field ID</th><th>Input Kind</th><th>Status</th></tr>
            </thead>
            <tbody>
              {draft_rows}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Run Events -->
      <div class="card">
        <div class="card-header">
          <div class="step-badge">i</div>
          <div class="card-header-text">
            <h2>Run Events</h2>
            <p>Audit trail of all pipeline events for this execution run</p>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Time</th><th>Event Type</th><th>Payload</th></tr>
            </thead>
            <tbody>
              {event_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
