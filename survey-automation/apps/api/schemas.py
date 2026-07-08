from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    run_id: str
    survey_id: str = "usnews_main"
    survey_year: int = 2026


class DispatchValidateRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"


class DispatchValidateResponse(BaseModel):
    run_id: str
    task_ids: list[str]
    workflow_ids: list[str]
    chunk_total: int


class DispatchScanFieldsRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"


class DispatchScanFieldsResponse(BaseModel):
    run_id: str
    task_id: str
    workflow_id: str
    status: str


class DispatchFillRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"
    scan_id: str | None = None


class DispatchFillResponse(BaseModel):
    run_id: str
    task_ids: list[str]
    workflow_ids: list[str]
    chunk_total: int
    submit_enabled: bool


class SkyvernWebhookResponse(BaseModel):
    ok: bool
    run_id: str
    workflow_id: str
    mismatches_created: int


class ReviewItemResponse(BaseModel):
    review_item_id: str
    field_id: str
    reason_code: str
    expected_value: str
    observed_value: str
    screenshot_url: str
    status: str


class RunEventResponse(BaseModel):
    event_type: str
    payload_json: str
    created_at: str


class FieldDiscoveryDraftResponse(BaseModel):
    draft_id: str
    section_id: str
    label_text: str
    input_kind: str
    required_flag: bool
    candidate_field_id: str
    status: str
    notes: str
    screenshot_url: str


class ApproveFieldDiscoveryRequest(BaseModel):
    field_id_override: str | None = None
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str = "survey_year"


class RejectFieldDiscoveryRequest(BaseModel):
    notes: str = "Rejected by analyst"


class CatalogFieldResponse(BaseModel):
    field_id: str
    section_id: str
    label_text: str
    input_kind: str
    required_flag: bool
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str
    transform_json: str
    status: str


class UpdateCatalogBindingRequest(BaseModel):
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str = "survey_year"
    transform_json: str = "{}"


class PrepareSectionPayloadRequest(BaseModel):
    section_id: str = "institution"
    create_missing_reviews: bool = True


class PrepareSectionPayloadResponse(BaseModel):
    run_id: str
    section_id: str
    resolved_values: dict[str, str]
    missing_fields: list[str]


class ExecuteSectionPipelineRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"


class ExecuteSectionPipelineResponse(BaseModel):
    run_id: str
    section_id: str
    scan_task_id: str
    validate_task_ids: list[str]
    resolved_field_count: int


class ExecuteDraftFillPipelineRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"
    scan_id: str | None = None


class ExecuteDraftFillPipelineResponse(BaseModel):
    run_id: str
    section_id: str
    fill_task_ids: list[str]
    fill_workflow_ids: list[str]
    field_count: int
    submit_enabled: bool


class RunMetricsResponse(BaseModel):
    tasks_total: int
    tasks_validate: int
    tasks_scan_fields: int
    tasks_fill: int
    split_rate_percent: int
    field_discovery_total: int
    field_discovery_approved: int
    iteration_failure_count: int


class StartWorkflowRequest(BaseModel):
    section_id: str = "institution"
    portal_url: str = "http://fake-form"
    callback_timeout_seconds: int = 1800
    workflow_mode: Literal["validate", "draft_fill"] = "validate"
    browser_session_id: str | None = None


class StartWorkflowResponse(BaseModel):
    run_id: str
    section_id: str
    started: bool


class PdfScanRequest(BaseModel):
    file_path: str
    survey_id: str = "uploaded_pdf"
    require_label_enrichment: bool = True
    allow_enrichment_fallback: bool = False
    label_enrichment_candidate_limit: int | None = None


class PdfCandidateResponse(BaseModel):
    candidate_id: str
    scan_id: str
    survey_id: str
    candidate_key: str
    source: str
    field_name: str
    label_text: str
    normalized_label: str
    input_kind: str
    page_number: int | None
    confidence: float
    label_source: str = ""
    field_rect: list[float] = []
    nearby_text: str = ""
    datapoint_intent: str = ""
    master_data_point_id: str
    status: str


class PdfScanResponse(BaseModel):
    scan_id: str
    survey_id: str
    file_name: str
    file_path: str
    file_sha256: str
    fillable: bool
    page_count: int
    candidate_count: int
    status: str
    created_at: str
    candidates: list[PdfCandidateResponse] = []


class MasterDataPointResponse(BaseModel):
    data_point_id: str
    canonical_name: str
    semantic_key: str
    description: str
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str
    transform_json: str
    status: str


class MasterDataPointAliasResponse(BaseModel):
    alias_id: str
    data_point_id: str
    alias_text: str
    normalized_alias: str
    source: str


class CreateMasterDataPointRequest(BaseModel):
    canonical_name: str
    semantic_key: str = ""
    description: str = ""
    databricks_view: str = ""
    databricks_value_column: str = ""
    databricks_year_column: str = ""
    transform_json: str = "{}"
    data_point_id: str | None = None


class UpdateMasterDataPointBindingRequest(BaseModel):
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str = "survey_year"
    transform_json: str = "{}"


class MapPdfCandidateRequest(BaseModel):
    master_data_point_id: str


class CreateMasterDataPointAliasRequest(BaseModel):
    alias_text: str
    source: str = "analyst"


class MappingSuggestionResponse(BaseModel):
    data_point_id: str
    canonical_name: str
    semantic_key: str
    score: int
    reason: str


class CandidateMappingSuggestionsResponse(BaseModel):
    candidate_id: str
    field_name: str
    label_text: str
    suggestions: list[MappingSuggestionResponse]


class ResolvePdfScanRequest(BaseModel):
    survey_year: int = 2026


class ResolvePdfScanResponse(BaseModel):
    scan_id: str
    survey_year: int
    values: dict[str, dict[str, str]]
    missing_candidates: list[str]
    unmapped_candidates: list[str]


class PublishPdfScanCatalogRequest(BaseModel):
    section_id: str | None = None
    overwrite: bool = True


class BootstrapMasterDataPointsRequest(BaseModel):
    section_id: str | None = None
    include_inactive: bool = False
    create_aliases: bool = True


class BootstrapFakeFormMasterDataPointsRequest(BaseModel):
    file_path: str = "/app/fake-survey-form/fake-survey-form-data.json"
    create_literal_bindings: bool = False


class BootstrapMasterDataPointsResponse(BaseModel):
    created_count: int
    reused_count: int
    alias_created_count: int
    data_point_ids: list[str]


class AutoMapPdfScanRequest(BaseModel):
    min_score: int = 70
    min_margin: int = 8
    include_already_mapped: bool = False
    add_alias_on_map: bool = False


class AutoMapPdfScanResponse(BaseModel):
    scan_id: str
    total_candidates: int
    mapped_count: int
    already_mapped_count: int
    skipped_no_suggestion: int
    skipped_low_score: int
    skipped_ambiguous: int
    mapped_candidate_ids: list[str]


class GenieDraftMappingsRequest(BaseModel):
    min_score: int = 70
    include_already_mapped: bool = False
    limit_candidates: int = 250
    provider: Literal["heuristic", "genie_api"] = "heuristic"
    overwrite_existing: bool = True
    genie_batch_size: int | None = None


class GenieDraftMappingItemResponse(BaseModel):
    draft_id: str
    candidate_id: str
    field_name: str
    label_text: str
    provider: str
    score: int
    status: str
    reason: str
    master_data_point_id: str
    databricks_view: str
    databricks_value_column: str
    databricks_year_column: str
    transform_json: str


class GenieDraftMappingsResponse(BaseModel):
    scan_id: str
    total_candidates: int
    drafted_count: int
    skipped_count: int
    skipped_already_mapped: int
    skipped_low_score: int
    drafts: list[GenieDraftMappingItemResponse]


class GenieDraftMappingsJobResponse(BaseModel):
    job_id: str
    scan_id: str
    status: str


class GenieDraftMappingsJobStatusResponse(BaseModel):
    job_id: str
    scan_id: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    request: dict[str, object]
    steps: list[dict[str, object]]
    result: dict[str, object] | None
    error: str | None


class GenieApiCallHistoryResponse(BaseModel):
    call_id: int
    job_id: str
    scan_id: str
    provider: str
    batch_index: int
    status: str
    request: dict[str, object] | list[dict[str, object]]
    response: dict[str, object] | list[dict[str, object]] | None
    error: str | None
    created_at: str


class ApprovePdfMappingDraftRequest(BaseModel):
    apply_binding: bool = True
    overwrite_master_binding: bool = False


class ApprovePdfMappingDraftResponse(BaseModel):
    draft: GenieDraftMappingItemResponse
    candidate_id: str
    master_data_point_id: str
    binding_applied: bool


class GenieResolveRequest(BaseModel):
    survey_year: int = 2024
    batch_size: int = 50
    min_confidence: int = 60
    force_regenie: bool = False


class GenieResolutionResult(BaseModel):
    scan_id: str
    survey_year: int
    resolved: int
    low_confidence: int
    failed: int
    skipped: int


class DirectResolveRequest(BaseModel):
    survey_year: int = 2024


class DirectResolutionResult(BaseModel):
    scan_id: str
    survey_year: int
    refreshed: int
    null_results: int
    sql_errors: int
    needs_regenie: int


class ResolvedValueResponse(BaseModel):
    candidate_id: str
    field_name: str
    label_text: str
    section: str
    datapoint_intent: str
    genie_value: str
    genie_confidence: int
    genie_sql_template: str
    genie_table: str
    genie_column: str
    genie_reason: str
    genie_resolved_at: str | None
    direct_sql_failures: int
    status: str


class AnalystSqlPreviewRequest(BaseModel):
    name: str = ""
    sql_text: str
    survey_year: int = 2025
    row_limit: int = Field(default=100, ge=1, le=5000)


class AnalystSqlPreviewResponse(BaseModel):
    query_id: str
    scan_id: str
    name: str
    survey_year: int
    columns: list[str]
    sample_rows: list[list[str]]
    row_count: int


class AnalystSqlAutoMapRequest(BaseModel):
    max_drafts: int = Field(default=50, ge=1, le=500)
    section_filter: str = Field(default="", description="CDS section id to restrict candidates (e.g. 'C', 'A', 'FRSH'). Leave blank to use all candidates.")


class CandidateSectionGroup(BaseModel):
    section_id: str
    section_label: str
    candidate_count: int
    candidates: list["PdfCandidateResponse"]


class CandidatesBySectionResponse(BaseModel):
    scan_id: str
    total_candidates: int
    sections: list[CandidateSectionGroup]


class AnalystSqlMappingDraftResponse(BaseModel):
    draft_id: str
    query_id: str
    scan_id: str
    candidate_id: str
    field_name: str
    label_text: str
    source_row_index: int
    source_column: str
    value_preview: str
    confidence: int
    reason: str
    status: str


class AnalystSqlAutoMapResponse(BaseModel):
    query_id: str
    scan_id: str
    drafted_count: int
    drafts: list[AnalystSqlMappingDraftResponse]


class ApproveAnalystSqlMappingDraftRequest(BaseModel):
    source_row_index: int | None = Field(default=None, ge=0)
    source_column: str | None = None


class ApproveAnalystSqlMappingDraftResponse(BaseModel):
    draft_id: str
    mapping_id: str
    query_id: str
    scan_id: str
    candidate_id: str
    field_name: str
    value: str
    status: str


class AnalystSqlRerunRequest(BaseModel):
    survey_year: int = 2025


class AnalystSqlRerunResponse(BaseModel):
    query_id: str
    scan_id: str
    survey_year: int
    refreshed: int
    missing: int
    sql_errors: int


class FillPreviewRow(BaseModel):
    field_key: str
    label: str = ""
    intent: str = ""
    section: str = ""
    pdf_value: str | None = None
    pdf_status: str | None = None
    pdf_confidence: int = 0
    web_value: str | None = None
    master_data_point_id: str | None = None
    databricks_view: str | None = None
    pdf_ready: bool = False
    web_ready: bool = False


class FillPreviewResponse(BaseModel):
    scan_id: str | None = None
    survey_id: str | None = None
    web_payload_path: str
    total_fields: int
    pdf_ready_count: int
    web_ready_count: int
    both_ready_count: int
    rows: list[FillPreviewRow]


class FilledPdfExportRequest(BaseModel):
    output_file_path: str | None = None
    flatten: bool = False


class FilledPdfExportResponse(BaseModel):
    scan_id: str
    source_file_path: str
    output_file_path: str
    download_url: str
    filled_count: int
    skipped_count: int
    missing_pdf_fields: list[str]
