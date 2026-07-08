from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    survey_id: Mapped[str] = mapped_column(String(64), nullable=False)
    survey_year: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SkyvernTask(Base):
    __tablename__ = "skyvern_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="dispatch_validate")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expected_values_json: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    screenshot_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class RunEvent(Base):
    __tablename__ = "run_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ReviewItem(Base):
    __tablename__ = "review_items"

    review_item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    field_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_value: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class FieldDiscoveryDraft(Base):
    __tablename__ = "field_discovery_drafts"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    portal_url: Mapped[str] = mapped_column(String(512), nullable=False)
    label_text: Mapped[str] = mapped_column(String(256), nullable=False)
    input_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    required_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_field_id: Mapped[str] = mapped_column(String(128), nullable=False)
    section_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    screenshot_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    result_json: Mapped[str] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class SurveyFieldCatalog(Base):
    __tablename__ = "survey_field_catalog"

    field_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label_text: Mapped[str] = mapped_column(String(256), nullable=False)
    input_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    required_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    databricks_view: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    databricks_value_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    databricks_year_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    transform_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class MasterDataPoint(Base):
    __tablename__ = "master_data_points"

    data_point_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    semantic_key: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    databricks_view: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    databricks_value_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    databricks_year_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    transform_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class MasterDataPointAlias(Base):
    __tablename__ = "master_data_point_aliases"

    alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_point_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    alias_text: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SurveyPdfScan(Base):
    __tablename__ = "survey_pdf_scans"

    scan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    survey_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fillable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SCANNED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class SurveyPdfDataPointCandidate(Base):
    __tablename__ = "survey_pdf_datapoint_candidates"

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    survey_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    field_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    label_text: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_label: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    input_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    label_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    field_rect_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    nearby_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    datapoint_intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    master_data_point_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    genie_sql_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    genie_table: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    genie_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    genie_year_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    genie_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    genie_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    genie_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    genie_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    direct_sql_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCOVERED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class PdfMappingDraft(Base):
    __tablename__ = "pdf_mapping_drafts"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    survey_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="heuristic")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_REVIEW")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    master_data_point_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    databricks_view: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    databricks_value_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    databricks_year_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    transform_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class GenieApiCallHistory(Base):
    __tablename__ = "genie_api_call_history"

    call_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="genie_api")
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    response_json: Mapped[str] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AnalystSqlQuery(Base):
    __tablename__ = "analyst_sql_queries"

    query_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    survey_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2025)
    columns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sample_rows_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PREVIEWED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AnalystSqlMappingDraft(Base):
    __tablename__ = "analyst_sql_mapping_drafts"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_row_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    value_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_APPROVAL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class AnalystSqlFieldMapping(Base):
    __tablename__ = "analyst_sql_field_mappings"

    mapping_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_row_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_column: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_from_draft_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
