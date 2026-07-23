from __future__ import annotations
from apps.api.pdf_page_context import (
    PdfPageContext,
    build_basic_page_context_from_candidates,
    load_page_context,
    page_context_paths,
    render_pdf_page_to_png,
)
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.databricks_genie_client import DatabricksGenieClient, GenieMappingChoice, GenieMappingClient
from apps.api.cds_query_registry import CdsQueryRegistry, apply_registry_year
from apps.api.databricks_resolver import DatabricksFieldResolver, ResolvedSectionPayload
from apps.api.databricks_metric_resolvers import resolver_name_for_fake_form_field, resolver_sources
from apps.api.db.models import (
    FieldDiscoveryDraft,
    MasterDataPoint,
    MasterDataPointAlias,
    PdfMappingDraft,
    ReviewItem,
    Run,
    RunEvent,
    SkyvernTask,
    SurveyFieldCatalog,
    SurveyPdfDataPointCandidate,
    SurveyPdfScan,
)
from apps.api.embedding_similarity import MappingSimilarityScorer, OpenAIEmbeddingSimilarityScorer
from apps.api.openai_pdf_label_enrichment import OpenAIPdfLabelEnricher, PdfLabelEnricher, PdfLabelEnrichment
from apps.api.pdf_scanner import _normalize_label, scan_pdf_datapoints
from apps.api.settings import Settings
from apps.skyvern_worker.skyvern_client import SkyvernClient
from apps.skyvern_worker.task_builder import (
    FieldDefinition,
    build_fill_task,
    build_scan_fields_task,
    build_validate_task,
    get_section_field_definitions,
    split_fields_for_task,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify_identifier(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")
    slug = re.sub(r"\.+", ".", slug)
    return slug[:96] or "data_point"


@dataclass(frozen=True)
class MasterMappingSuggestion:
    data_point_id: str
    canonical_name: str
    semantic_key: str
    score: int
    reason: str


@dataclass(frozen=True)
class CandidateMappingSuggestions:
    candidate_id: str
    field_name: str
    label_text: str
    suggestions: list[MasterMappingSuggestion]


@dataclass(frozen=True)
class ResolvedPdfScanPayload:
    scan_id: str
    survey_year: int
    values: dict[str, dict[str, str]]
    missing_candidates: list[str]
    unmapped_candidates: list[str]


@dataclass(frozen=True)
class FilledPdfExportResult:
    scan_id: str
    source_file_path: str
    output_file_path: str
    filled_count: int
    skipped_count: int
    missing_pdf_fields: list[str]


@dataclass(frozen=True)
class BootstrapMasterDataPointsResult:
    created_count: int
    reused_count: int
    alias_created_count: int
    data_point_ids: list[str]


@dataclass(frozen=True)
class AutoMapPdfScanResult:
    scan_id: str
    total_candidates: int
    mapped_count: int
    already_mapped_count: int
    skipped_no_suggestion: int
    skipped_low_score: int
    skipped_ambiguous: int
    mapped_candidate_ids: list[str]


@dataclass(frozen=True)
class PdfMappingDraftSuggestion:
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


@dataclass(frozen=True)
class GeneratePdfMappingDraftsResult:
    scan_id: str
    total_candidates: int
    drafted_count: int
    skipped_count: int
    skipped_already_mapped: int
    skipped_low_score: int
    drafts: list[PdfMappingDraftSuggestion]


@dataclass(frozen=True)
class ApprovePdfMappingDraftResult:
    draft: PdfMappingDraftSuggestion
    candidate_id: str
    master_data_point_id: str
    binding_applied: bool


class PdfLabelEnrichmentFailedError(RuntimeError):
    def __init__(self, *, provider: str, reason: str) -> None:
        super().__init__(reason)
        self.provider = provider
        self.reason = reason


def _score_candidate_against_master(
    candidate: SurveyPdfDataPointCandidate,
    master: MasterDataPoint,
    aliases: list[MasterDataPointAlias],
    semantic_scorer: MappingSimilarityScorer | None = None,
) -> MasterMappingSuggestion | None:
    candidate_texts = [
        ("label", candidate.normalized_label or _normalize_label(candidate.label_text)),
        ("nearby_text", _normalize_label(candidate.nearby_text)),
        ("field_name", _normalize_label(candidate.field_name.replace("_", " "))),
    ]
    master_texts = [
        ("canonical", _normalize_label(master.canonical_name)),
        ("semantic_key", _normalize_label(master.semantic_key.replace(".", " "))),
    ]
    master_texts.extend(("alias", alias.normalized_alias or _normalize_label(alias.alias_text)) for alias in aliases)

    best_score = 0
    best_reason = ""
    for candidate_source, candidate_text in candidate_texts:
        if not candidate_text:
            continue
        for master_source, master_text in master_texts:
            if not master_text:
                continue
            score = _text_match_score(candidate_text, master_text)
            if score > best_score:
                best_score = score
                best_reason = f"{candidate_source} matched {master_source}"

    if semantic_scorer:
        embedding_score = semantic_scorer.score_pair(
            _candidate_similarity_text(candidate),
            _master_similarity_text(master, aliases),
        )
        if embedding_score is not None and embedding_score > best_score:
            best_score = embedding_score
            best_reason = "embedding similarity match"

    if best_score == 0:
        return None
    return MasterMappingSuggestion(
        data_point_id=master.data_point_id,
        canonical_name=master.canonical_name,
        semantic_key=master.semantic_key,
        score=best_score,
        reason=best_reason,
    )


def _text_match_score(left: str, right: str) -> int:
    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return 0
    if left == right:
        return 100
    if _has_meaningful_phrase_containment(left_tokens, right_tokens):
        return 88
    intersection = left_tokens & right_tokens
    if not intersection:
        return 0
    if len(intersection) == 1 and (len(left_tokens) > 1 or len(right_tokens) > 1):
        return 60
    recall = len(intersection) / len(right_tokens)
    precision = len(intersection) / len(left_tokens)
    score = int(100 * ((0.65 * recall) + (0.35 * precision)))
    return min(score, 84)


def _has_meaningful_phrase_containment(left_tokens: set[str], right_tokens: set[str]) -> bool:
    shorter = left_tokens if len(left_tokens) <= len(right_tokens) else right_tokens
    longer = right_tokens if shorter is left_tokens else left_tokens
    return len(shorter) >= 2 and shorter.issubset(longer)


def _meaningful_tokens(value: str) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "by",
        "for",
        "from",
        "if",
        "in",
        "is",
        "of",
        "or",
        "the",
        "to",
        "what",
        "yes",
    }
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if token not in stop_words and len(token) > 1}


def _candidate_similarity_text(candidate: SurveyPdfDataPointCandidate) -> str:
    parts = [candidate.label_text, candidate.nearby_text, candidate.field_name.replace("_", " ")]
    return " | ".join(part.strip() for part in parts if part and part.strip())


def _enriched_nearby_text(original_nearby_text: str, enrichment: PdfLabelEnrichment | None) -> str:
    if not enrichment:
        return original_nearby_text
    parts = [
        enrichment.context or enrichment.nearby_text,
        f"Section: {enrichment.section}" if enrichment.section else (
            f"LLM section: {enrichment.section_hint}" if enrichment.section_hint else ""
        ),
        f"Original nearby text: {original_nearby_text}" if original_nearby_text else "",
    ]
    return " | ".join(part.strip() for part in parts if part and part.strip())


_EXPECTED_VALUE_TYPE_TO_INPUT_KIND: dict[str, str] = {
    "boolean": "checkbox",
    "integer": "number",
    "decimal": "number",
    "text": "text",
    "select": "select",
}


def _resolve_input_kind(enrichment: PdfLabelEnrichment | None, fallback: str) -> str:
    if enrichment:
        if enrichment.expected_value_type:
            mapped = _EXPECTED_VALUE_TYPE_TO_INPUT_KIND.get(enrichment.expected_value_type.lower())
            if mapped:
                return mapped
        if enrichment.input_kind:
            return enrichment.input_kind
    return fallback or "text"


def _master_similarity_text(master: MasterDataPoint, aliases: list[MasterDataPointAlias]) -> str:
    alias_parts = [alias.alias_text for alias in aliases if alias.alias_text.strip()]
    parts = [master.canonical_name, master.semantic_key.replace(".", " "), master.description, *alias_parts]
    return " | ".join(part.strip() for part in parts if part and part.strip())


def _humanize_identifier(value: str) -> str:
    replacements = {
        "ft": "Full-time",
        "pt": "Part-time",
        "ftf": "first-time freshman",
        "fyft": "first-year full-time",
        "ug": "undergraduate",
        "grad": "graduate",
        "gpa": "GPA",
        "pct": "percent",
        "intl": "international",
        "hs": "high school",
        "gi": "GI",
    }
    words = []
    for token in value.replace(".", "_").split("_"):
        if not token:
            continue
        words.append(replacements.get(token, token))
    label = " ".join(words).strip()
    return label[:1].upper() + label[1:] if label else value


def _fake_form_aliases(field_key: str) -> list[str]:
    base = _humanize_identifier(field_key)
    aliases = [base, field_key.replace("_", " ")]
    explicit = {
        "institution_name": ["Institution Name", "Name of Institution", "CDS Name"],
        "display_name": ["Display Name", "Common Data Set display name"],
        "mailing_address": ["Address", "Mailing Address", "CDS Address"],
        "city": ["City", "CDS City"],
        "state": ["State", "CDS State"],
        "zip": ["Zip", "Zip Code", "Postal Code"],
        "main_phone": ["Office Phone", "Main Phone", "CDS Phone"],
        "homepage": ["Homepage", "Institution Website", "Main Institution Website", "CDS URL"],
        "control": ["Control", "Institution Control"],
        "calendar": ["Academic Calendar", "Calendar System"],
        "applied_total": ["Total applicants", "Applied total", "Applications total"],
        "admitted_total": ["Total admitted", "Admissions total", "Admitted total"],
        "enrolled_total": ["Total enrolled", "Enrolled total"],
        "total_undergraduates": ["Total undergraduates", "Total undergraduate enrollment"],
        "total_graduates": ["Total graduates", "Total graduate enrollment"],
        "grand_total_enrollment": ["Grand total enrollment", "Total enrollment"],
    }
    aliases.extend(explicit.get(field_key, []))
    return list(dict.fromkeys(alias for alias in aliases if alias.strip()))


def _field_catalog_id_for_pdf_candidate(survey_id: str, candidate_key: str) -> str:
    candidate_parts = [part for part in candidate_key.split(".") if part]
    parts = ["pdf", _safe_field_id_part(survey_id), *[_safe_field_id_part(part) for part in candidate_parts]]
    return ".".join(parts)[:128]


def _infer_fake_form_field_key_from_candidate(candidate: SurveyPdfDataPointCandidate) -> str | None:
    field = candidate.field_name.strip().upper()
    if not field:
        return None
    enrollment_map = {
        "EN_TOT_UG_N": "total_undergraduates",
        "EN_TOT_GR_N": "total_graduates",
        "EN_TOT_N": "grand_total_enrollment",
        "EN_TOT_1ST_N": "enrolled_total",
        "EN_TOT_MEN_1ST_N": "enrolled_men",
        "EN_TOT_WOMEN_1ST_N": "enrolled_women",
        "EN_TOT_ANOTHER_GENDER_1ST_N": "enrolled_other",
    }
    admissions_map = {
        "AP_RECD_1ST_N": "applied_total",
        "AP_RECD_MEN_1ST_N": "applied_men",
        "AP_RECD_WOMEN_1ST_N": "applied_women",
        "AP_RECD_ANOTHER_GENDER_1ST_N": "applied_other",
        "AP_ADMT_1ST_N": "admitted_total",
        "AP_ADMT_MEN_1ST_N": "admitted_men",
        "AP_ADMT_WOMEN_1ST_N": "admitted_women",
        "AP_ADMT_ANOTHER_GENDER_1ST_N": "admitted_other",
    }
    if field in enrollment_map:
        return enrollment_map[field]
    if field in admissions_map:
        return admissions_map[field]
    if field.startswith("EN_UG_"):
        return "total_undergraduates"
    if field.startswith("EN_") and "GR" in field:
        return "total_graduates"
    if field.startswith("EN_"):
        return "enrolled_total"
    if field.startswith("AP_RECD_"):
        return "applied_total"
    if field.startswith("AP_ADMT_"):
        return "admitted_total"
    if field.startswith("URL_DESTINATION"):
        return "homepage"
    return None


def _build_draft_binding_for_candidate(
    candidate: SurveyPdfDataPointCandidate,
    suggestion: MasterMappingSuggestion,
    master: MasterDataPoint,
) -> tuple[str, str, str, str]:
    if master.databricks_view or master.databricks_value_column:
        return (
            master.databricks_view,
            master.databricks_value_column,
            master.databricks_year_column,
            master.transform_json or "{}",
        )

    field_key = _infer_fake_form_field_key_from_candidate(candidate)
    if not field_key:
        return ("", "", "", "{}")
    resolver_name = resolver_name_for_fake_form_field(field_key)
    if not resolver_name:
        return ("", "", "", "{}")
    sources = resolver_sources(resolver_name)
    transform = {
        "source": "genie_draft_heuristic",
        "field_key": field_key,
        "resolver_name": resolver_name,
        "resolver_field": field_key,
        "databricks_sources": sources,
    }
    return (",".join(sources), "", "", json.dumps(transform))


def _build_binding_from_field_key(field_key: str) -> tuple[str, str, str, str]:
    resolver_name = resolver_name_for_fake_form_field(field_key)
    if not resolver_name:
        return ("", "", "", "{}")
    sources = resolver_sources(resolver_name)
    transform = {
        "source": "genie_draft",
        "field_key": field_key,
        "resolver_name": resolver_name,
        "resolver_field": field_key,
        "databricks_sources": sources,
    }
    return (",".join(sources), "", "", json.dumps(transform))


def _draft_to_suggestion(
    draft: PdfMappingDraft,
    *,
    field_name: str,
    label_text: str,
) -> PdfMappingDraftSuggestion:
    return PdfMappingDraftSuggestion(
        draft_id=draft.draft_id,
        candidate_id=draft.candidate_id,
        field_name=field_name,
        label_text=label_text,
        provider=draft.provider,
        score=draft.score,
        status=draft.status,
        reason=draft.reason,
        master_data_point_id=draft.master_data_point_id,
        databricks_view=draft.databricks_view,
        databricks_value_column=draft.databricks_value_column,
        databricks_year_column=draft.databricks_year_column,
        transform_json=draft.transform_json,
    )


def _safe_field_id_part(value: str) -> str:
    part = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return part or "field"


def _catalog_input_kind(input_kind: str) -> str:
    normalized = input_kind.strip().lower()
    if normalized in {"text", "number", "select", "radio", "checkbox", "checkbox_group", "date", "textarea"}:
        return normalized
    return "text"


def _decode_cds_pdf_field_name(field_name: str) -> str:
    tokens = field_name.upper().split("_")
    meanings = {
        "CDS": "Common Data Set",
        "EN": "enrollment",
        "UG": "undergraduate",
        "GRAD": "graduate",
        "TOT": "total",
        "DEG": "degree-seeking",
        "CRDT": "credit-seeking / for credit",
        "FT": "full-time",
        "PT": "part-time",
        "MEN": "men",
        "WOMEN": "women",
        "UNK": "unknown or another gender category",
        "N": "count",
        "AP": "admissions/applicants",
        "RECD": "applications received",
        "ADMT": "admitted",
        "1ST": "first-time first-year",
        "RES": "resident",
        "NONRES": "nonresident",
        "INTL": "international/nonresident",
    }
    return " / ".join(meanings.get(token, token.lower()) for token in tokens if token)


def _page_context_to_text(page_context: PdfPageContext | None) -> str:
    if not page_context:
        return ""

    extracted = page_context.extracted_context or {}
    labels = extracted.get("labels", [])
    field_names = extracted.get("field_names", [])
    nearby_samples = extracted.get("nearby_text_samples", [])

    parts = [
        f"Cached page context for page {page_context.page_number}: {page_context.page_summary[:500]}",
    ]

    if page_context.section_title:
        parts.append(f"Section: {page_context.section_title[:200]}")

    if page_context.table_titles:
        parts.append(f"Tables: {', '.join(page_context.table_titles[:3])[:300]}")

    if labels:
        parts.append(f"Key labels on page: {', '.join(labels[:12])[:500]}")

    if field_names:
        parts.append(f"Key field names on page: {', '.join(field_names[:12])[:500]}")

    if nearby_samples:
        cleaned_samples = [" ".join(str(item).split())[:180] for item in nearby_samples[:3]]
        parts.append(f"Nearby samples: {' | '.join(cleaned_samples)[:700]}")

    return "\n".join(parts)[:2500]

def _build_genie_candidate_from_pdf_candidate(
    candidate: SurveyPdfDataPointCandidate,
    page_context: PdfPageContext | None = None,
) -> dict[str, Any]:
    decoded_field_name = _decode_cds_pdf_field_name(candidate.field_name)
    page_context_text = _page_context_to_text(page_context)

    rich_label = (
        f"{candidate.label_text or candidate.field_name} | "
        f"PDF field: {candidate.field_name} | "
        f"Meaning: {decoded_field_name}"
    )

    context_parts = [
        f"PDF field name: {candidate.field_name}",
        f"Decoded PDF field meaning: {decoded_field_name}",
        f"Visible label: {candidate.label_text}",
    ]

    if candidate.datapoint_intent:
        context_parts.append(f"PDF datapoint intent: {candidate.datapoint_intent}")

    if candidate.nearby_text:
        context_parts.append(f"Field nearby PDF/table text: {candidate.nearby_text}")

    if candidate.candidate_key:
        context_parts.append(f"PDF candidate key: {candidate.candidate_key}")

    if candidate.page_number:
        context_parts.append(f"PDF page number: {candidate.page_number}")

    if page_context_text:
        context_parts.append(page_context_text)

    context_parts.append(
        "Instructions: This is a Common Data Set PDF field. "
        "The visible label may be repeated or incomplete because this field may be inside a table. "
        "Use field name, decoded meaning, nearby text, and page context together. "
        "For enrollment, avoid duplicate row counting; prefer official headcount or distinct student count when needed. "
        "Return the value for this exact field."
    )
    rich_context = "\n".join(part for part in context_parts if part)
    rich_context = rich_context[:3500]

    return {
        "candidate_id": candidate.candidate_id,
        "field_name": candidate.field_name,
        "label_text": rich_label[:1000],
        "nearby_text": rich_context,
        "page_number": candidate.page_number or 1,
        "field_type": candidate.input_kind or "text",
        "options": [],
    }

class PdfDatapointService:
    def __init__(
        self,
        session: Session,
        mapping_similarity_scorer: MappingSimilarityScorer | None = None,
        settings: Settings | None = None,
        genie_mapping_client: GenieMappingClient | None = None,
        pdf_label_enricher: PdfLabelEnricher | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or Settings()
        self._mapping_similarity_scorer = mapping_similarity_scorer or OpenAIEmbeddingSimilarityScorer(self._settings)
        self._genie_mapping_client = genie_mapping_client or DatabricksGenieClient(self._settings)
        self._pdf_label_enricher = pdf_label_enricher or OpenAIPdfLabelEnricher(self._settings)

    def scan_pdf(
        self,
        *,
        file_path: str,
        survey_id: str,
        require_label_enrichment: bool = True,
        allow_enrichment_fallback: bool = False,
        label_enrichment_candidate_limit: int | None = None,
    ) -> tuple[SurveyPdfScan, list[SurveyPdfDataPointCandidate]]:
        result = scan_pdf_datapoints(Path(file_path), survey_id=survey_id)
        provider = "openai"
        enrichments: dict[str, PdfLabelEnrichment] = {}
        if require_label_enrichment:
            limit = label_enrichment_candidate_limit
            if limit is None:
                limit = self._settings.pdf_label_enrichment_candidate_limit
            limit = max(0, int(limit))
            candidates_for_enrichment = result.candidates[:limit] if limit else []
            configured = bool(getattr(self._pdf_label_enricher, "configured", True))
            if not configured and not allow_enrichment_fallback:
                raise PdfLabelEnrichmentFailedError(
                    provider=provider,
                    reason=f"Label enrichment provider '{provider}' is not configured",
                )
            if configured:
                try:
                    enrichments = self._pdf_label_enricher.enrich_pdf(
                        file_path=result.file_path,
                        candidates=candidates_for_enrichment,
                        page_count=result.page_count,
                    )
                except Exception as exc:  # noqa: BLE001
                    if not allow_enrichment_fallback:
                        raise PdfLabelEnrichmentFailedError(
                            provider=provider,
                            reason=str(exc),
                        ) from exc
                if not enrichments and candidates_for_enrichment and not allow_enrichment_fallback:
                    response_summary = str(getattr(self._pdf_label_enricher, "last_response_summary", "") or "").strip()
                    reason = "Label enrichment returned no mappings"
                    if response_summary:
                        reason = f"{reason}. {response_summary}"
                    raise PdfLabelEnrichmentFailedError(
                        provider=provider,
                        reason=reason,
                    )
        scan = SurveyPdfScan(
            scan_id=f"pdfscan_{uuid.uuid4().hex[:12]}",
            survey_id=survey_id,
            file_name=result.file_name,
            file_path=result.file_path,
            file_sha256=result.file_sha256,
            fillable=result.fillable,
            page_count=result.page_count,
            candidate_count=len(result.candidates),
            raw_result_json=json.dumps(result.to_dict()),
            status="SCANNED",
            created_at=_utc_now(),
        )
        self._session.add(scan)

        candidates: list[SurveyPdfDataPointCandidate] = []
        for candidate in result.candidates:
            enrichment = enrichments.get(candidate.candidate_key)
            label_text = enrichment.label_text if enrichment else candidate.label_text
            nearby_text = _enriched_nearby_text(candidate.nearby_text, enrichment)
            input_kind = _resolve_input_kind(enrichment, candidate.input_kind)
            confidence = max(candidate.confidence, enrichment.confidence if enrichment else 0.0)
            label_source = self._enrichment_label_source() if enrichment else candidate.label_source
            datapoint_intent = enrichment.datapoint_intent if enrichment else ""
            row = SurveyPdfDataPointCandidate(
                candidate_id=f"pdfcand_{uuid.uuid4().hex[:12]}",
                scan_id=scan.scan_id,
                survey_id=survey_id,
                candidate_key=candidate.candidate_key,
                source=candidate.source,
                field_name=candidate.field_name,
                label_text=label_text,
                normalized_label=_normalize_label(label_text),
                input_kind=input_kind,
                page_number=candidate.page_number,
                confidence=confidence,
                label_source=label_source,
                field_rect_json=json.dumps(candidate.field_rect or []),
                nearby_text=nearby_text,
                datapoint_intent=datapoint_intent,
                master_data_point_id="",
                status="DISCOVERED",
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            self._session.add(row)
            candidates.append(row)
        self._session.commit()
        return scan, candidates

    def _enrichment_label_source(self) -> str:
        return "openai_enriched"

    def get_pdf_scan(self, scan_id: str) -> SurveyPdfScan:
        scan = self._session.get(SurveyPdfScan, scan_id)
        if not scan:
            raise ValueError(f"Unknown scan_id: {scan_id}")
        return scan

    def list_pdf_scans(self) -> list[SurveyPdfScan]:
        query = select(SurveyPdfScan).order_by(SurveyPdfScan.created_at.desc())
        return list(self._session.execute(query).scalars())

    def delete_pdf_scan(self, scan_id: str) -> None:
        scan = self._session.get(SurveyPdfScan, scan_id)
        if not scan:
            raise ValueError(f"Unknown scan_id: {scan_id}")
        for candidate in list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate).where(SurveyPdfDataPointCandidate.scan_id == scan_id)
            ).scalars()
        ):
            self._session.delete(candidate)
        self._session.delete(scan)
        self._session.commit()

    def list_pdf_candidates(self, scan_id: str) -> list[SurveyPdfDataPointCandidate]:
        self.get_pdf_scan(scan_id)
        query = (
            select(SurveyPdfDataPointCandidate)
            .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
            .order_by(SurveyPdfDataPointCandidate.page_number, SurveyPdfDataPointCandidate.label_text)
        )
        return list(self._session.execute(query).scalars())

    def list_master_data_points(self) -> list[MasterDataPoint]:
        query = select(MasterDataPoint).order_by(MasterDataPoint.semantic_key, MasterDataPoint.canonical_name)
        return list(self._session.execute(query).scalars())

    def bootstrap_master_data_points_from_catalog(
        self,
        *,
        section_id: str | None = None,
        include_inactive: bool = False,
        create_aliases: bool = True,
    ) -> BootstrapMasterDataPointsResult:
        query = select(SurveyFieldCatalog)
        if section_id:
            query = query.where(SurveyFieldCatalog.section_id == section_id.strip())
        if not include_inactive:
            query = query.where(SurveyFieldCatalog.status == "ACTIVE")
        catalog_rows = list(self._session.execute(query.order_by(SurveyFieldCatalog.section_id, SurveyFieldCatalog.field_id)).scalars())

        masters_by_id = {row.data_point_id: row for row in self.list_master_data_points()}
        masters_by_semantic = {row.semantic_key.strip().lower(): row for row in masters_by_id.values() if row.semantic_key.strip()}
        masters_by_canonical = {
            _normalize_label(row.canonical_name): row for row in masters_by_id.values() if row.canonical_name.strip()
        }
        aliases_by_master = self._aliases_by_data_point_id()

        created_count = 0
        reused_count = 0
        alias_created_count = 0
        touched_ids: list[str] = []

        for catalog in catalog_rows:
            semantic_key = catalog.field_id.strip()
            canonical_name = catalog.label_text.strip() or catalog.field_id.strip()
            preferred_id = f"dp.catalog.{_slugify_identifier(catalog.field_id)}"
            master = (
                masters_by_id.get(preferred_id)
                or masters_by_semantic.get(semantic_key.lower())
                or masters_by_canonical.get(_normalize_label(canonical_name))
            )

            if not master:
                master = MasterDataPoint(
                    data_point_id=preferred_id,
                    canonical_name=canonical_name,
                    semantic_key=semantic_key,
                    description=f"Bootstrapped from survey_field_catalog:{catalog.field_id}",
                    databricks_view=catalog.databricks_view,
                    databricks_value_column=catalog.databricks_value_column,
                    databricks_year_column=catalog.databricks_year_column,
                    transform_json=catalog.transform_json or "{}",
                    status="ACTIVE",
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                self._session.add(master)
                masters_by_id[master.data_point_id] = master
                if master.semantic_key.strip():
                    masters_by_semantic[master.semantic_key.strip().lower()] = master
                masters_by_canonical[_normalize_label(master.canonical_name)] = master
                aliases_by_master.setdefault(master.data_point_id, [])
                created_count += 1
            else:
                reused_count += 1

            touched_ids.append(master.data_point_id)
            if create_aliases:
                alias_created_count += self._ensure_master_alias(
                    data_point_id=master.data_point_id,
                    alias_text=catalog.label_text,
                    source="bootstrap-catalog",
                    aliases_by_master=aliases_by_master,
                )
                alias_created_count += self._ensure_master_alias(
                    data_point_id=master.data_point_id,
                    alias_text=catalog.field_id.replace(".", " ").replace("_", " "),
                    source="bootstrap-field-id",
                    aliases_by_master=aliases_by_master,
                )
        self._session.commit()
        return BootstrapMasterDataPointsResult(
            created_count=created_count,
            reused_count=reused_count,
            alias_created_count=alias_created_count,
            data_point_ids=sorted(set(touched_ids)),
        )

    def bootstrap_master_data_points_from_fake_form_data(
        self,
        *,
        file_path: str,
        create_literal_bindings: bool = False,
    ) -> BootstrapMasterDataPointsResult:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Fake form data file not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Fake form data must be a JSON object")

        masters_by_id = {row.data_point_id: row for row in self.list_master_data_points()}
        aliases_by_master = self._aliases_by_data_point_id()
        created_count = 0
        reused_count = 0
        alias_created_count = 0
        touched_ids: list[str] = []

        for field_key, value in sorted(payload.items()):
            if field_key.startswith("__"):
                continue
            if isinstance(value, dict):
                continue
            canonical_name = _humanize_identifier(field_key)
            data_point_id = f"dp.fake_form.{_slugify_identifier(field_key)}"
            master = masters_by_id.get(data_point_id)
            if not master:
                literal_value = ""
                if create_literal_bindings and not isinstance(value, list):
                    literal_value = f"literal:{value}"
                resolver_name = resolver_name_for_fake_form_field(field_key)
                sources = resolver_sources(resolver_name) if resolver_name else []
                transform = {"source": "fake_form_bootstrap", "field_key": field_key}
                if resolver_name:
                    transform.update(
                        {
                            "resolver_name": resolver_name,
                            "resolver_field": field_key,
                            "databricks_sources": sources,
                        }
                    )
                master = MasterDataPoint(
                    data_point_id=data_point_id,
                    canonical_name=canonical_name,
                    semantic_key=f"fake_form.{field_key}",
                    description=f"Bootstrapped from fake-survey-form-data.json field {field_key}",
                    databricks_view=",".join(sources),
                    databricks_value_column=literal_value,
                    databricks_year_column="",
                    transform_json=json.dumps(transform),
                    status="ACTIVE",
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                self._session.add(master)
                masters_by_id[data_point_id] = master
                aliases_by_master.setdefault(data_point_id, [])
                created_count += 1
            else:
                resolver_name = resolver_name_for_fake_form_field(field_key)
                if resolver_name and not create_literal_bindings:
                    sources = resolver_sources(resolver_name)
                    master.databricks_view = ",".join(sources)
                    master.databricks_value_column = ""
                    master.databricks_year_column = ""
                    master.transform_json = json.dumps(
                        {
                            "source": "fake_form_bootstrap",
                            "field_key": field_key,
                            "resolver_name": resolver_name,
                            "resolver_field": field_key,
                            "databricks_sources": sources,
                        }
                    )
                    master.updated_at = _utc_now()
                reused_count += 1

            touched_ids.append(data_point_id)
            for alias_text in _fake_form_aliases(field_key):
                alias_created_count += self._ensure_master_alias(
                    data_point_id=data_point_id,
                    alias_text=alias_text,
                    source="bootstrap-fake-form",
                    aliases_by_master=aliases_by_master,
                )

        self._session.commit()
        return BootstrapMasterDataPointsResult(
            created_count=created_count,
            reused_count=reused_count,
            alias_created_count=alias_created_count,
            data_point_ids=sorted(set(touched_ids)),
        )

    def list_master_aliases(self, data_point_id: str) -> list[MasterDataPointAlias]:
        if not self._session.get(MasterDataPoint, data_point_id):
            raise ValueError(f"Unknown data_point_id: {data_point_id}")
        query = (
            select(MasterDataPointAlias)
            .where(MasterDataPointAlias.data_point_id == data_point_id)
            .order_by(MasterDataPointAlias.alias_text)
        )
        return list(self._session.execute(query).scalars())

    def add_master_alias(self, *, data_point_id: str, alias_text: str, source: str = "analyst") -> MasterDataPointAlias:
        if not self._session.get(MasterDataPoint, data_point_id):
            raise ValueError(f"Unknown data_point_id: {data_point_id}")
        alias_text = alias_text.strip()
        if not alias_text:
            raise ValueError("alias_text is required")
        row = MasterDataPointAlias(
            alias_id=f"alias_{uuid.uuid4().hex[:12]}",
            data_point_id=data_point_id,
            alias_text=alias_text,
            normalized_alias=_normalize_label(alias_text),
            source=source.strip() or "analyst",
            created_at=_utc_now(),
        )
        self._session.add(row)
        self._session.commit()
        return row

    def create_master_data_point(
        self,
        *,
        canonical_name: str,
        semantic_key: str,
        description: str,
        databricks_view: str,
        databricks_value_column: str,
        databricks_year_column: str,
        transform_json: str,
        data_point_id: str | None = None,
    ) -> MasterDataPoint:
        canonical_name = canonical_name.strip()
        if not canonical_name:
            raise ValueError("canonical_name is required")
        candidate_id = (data_point_id or f"dp.{_slugify_identifier(semantic_key or canonical_name)}").strip()
        if self._session.get(MasterDataPoint, candidate_id):
            raise ValueError(f"data_point_id already exists: {candidate_id}")
        row = MasterDataPoint(
            data_point_id=candidate_id,
            canonical_name=canonical_name,
            semantic_key=semantic_key.strip(),
            description=description.strip(),
            databricks_view=databricks_view.strip(),
            databricks_value_column=databricks_value_column.strip(),
            databricks_year_column=databricks_year_column.strip(),
            transform_json=transform_json.strip() or "{}",
            status="ACTIVE",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        self._session.add(row)
        self._session.commit()
        return row

    def update_master_databricks_binding(
        self,
        *,
        data_point_id: str,
        databricks_view: str,
        databricks_value_column: str,
        databricks_year_column: str,
        transform_json: str,
    ) -> MasterDataPoint:
        row = self._session.get(MasterDataPoint, data_point_id)
        if not row:
            raise ValueError(f"Unknown data_point_id: {data_point_id}")
        row.databricks_view = databricks_view.strip()
        row.databricks_value_column = databricks_value_column.strip()
        row.databricks_year_column = databricks_year_column.strip()
        row.transform_json = transform_json.strip() or "{}"
        row.updated_at = _utc_now()
        self._session.commit()
        return row

    def map_pdf_candidate(self, *, candidate_id: str, master_data_point_id: str) -> SurveyPdfDataPointCandidate:
        candidate = self._session.get(SurveyPdfDataPointCandidate, candidate_id)
        if not candidate:
            raise ValueError(f"Unknown candidate_id: {candidate_id}")
        if not self._session.get(MasterDataPoint, master_data_point_id):
            raise ValueError(f"Unknown master_data_point_id: {master_data_point_id}")
        candidate.master_data_point_id = master_data_point_id
        candidate.status = "MAPPED"
        candidate.updated_at = _utc_now()
        self._session.commit()
        return candidate

    def suggest_candidate_mappings(self, *, scan_id: str, limit_per_candidate: int = 3) -> list["CandidateMappingSuggestions"]:
        candidates = self.list_pdf_candidates(scan_id)
        masters = self.list_master_data_points()
        aliases = self._aliases_by_data_point_id()
        results: list[CandidateMappingSuggestions] = []
        for candidate in candidates:
            scored: list[MasterMappingSuggestion] = []
            for master in masters:
                suggestion = _score_candidate_against_master(
                    candidate,
                    master,
                    aliases.get(master.data_point_id, []),
                    self._mapping_similarity_scorer,
                )
                if suggestion and suggestion.score >= 25:
                    scored.append(suggestion)
            scored.sort(key=lambda item: (-item.score, item.data_point_id))
            results.append(
                CandidateMappingSuggestions(
                    candidate_id=candidate.candidate_id,
                    field_name=candidate.field_name,
                    label_text=candidate.label_text,
                    suggestions=scored[: max(1, limit_per_candidate)],
                )
            )
        return results

    def auto_map_pdf_scan_candidates(
        self,
        *,
        scan_id: str,
        min_score: int = 70,
        min_margin: int = 8,
        include_already_mapped: bool = False,
        add_alias_on_map: bool = False,
    ) -> AutoMapPdfScanResult:
        min_score = max(0, min(100, min_score))
        min_margin = max(0, min_margin)
        candidates = self.list_pdf_candidates(scan_id)
        masters = self.list_master_data_points()
        aliases_by_master = self._aliases_by_data_point_id()

        mapped_count = 0
        already_mapped_count = 0
        skipped_no_suggestion = 0
        skipped_low_score = 0
        skipped_ambiguous = 0
        mapped_candidate_ids: list[str] = []

        for candidate in candidates:
            if candidate.master_data_point_id and not include_already_mapped:
                already_mapped_count += 1
                continue
            scored: list[MasterMappingSuggestion] = []
            for master in masters:
                suggestion = _score_candidate_against_master(
                    candidate,
                    master,
                    aliases_by_master.get(master.data_point_id, []),
                    self._mapping_similarity_scorer,
                )
                if suggestion and suggestion.score >= 25:
                    scored.append(suggestion)
            scored.sort(key=lambda item: (-item.score, item.data_point_id))
            if not scored:
                skipped_no_suggestion += 1
                continue
            top = scored[0]
            second_score = scored[1].score if len(scored) > 1 else 0
            if top.score < min_score:
                skipped_low_score += 1
                continue
            if len(scored) > 1 and (top.score - second_score) < min_margin:
                skipped_ambiguous += 1
                continue
            candidate.master_data_point_id = top.data_point_id
            candidate.status = "MAPPED"
            candidate.updated_at = _utc_now()
            mapped_candidate_ids.append(candidate.candidate_id)
            mapped_count += 1
            if add_alias_on_map:
                self._ensure_master_alias(
                    data_point_id=top.data_point_id,
                    alias_text=candidate.label_text,
                    source="auto-map",
                    aliases_by_master=aliases_by_master,
                )

        self._session.commit()
        return AutoMapPdfScanResult(
            scan_id=scan_id,
            total_candidates=len(candidates),
            mapped_count=mapped_count,
            already_mapped_count=already_mapped_count,
            skipped_no_suggestion=skipped_no_suggestion,
            skipped_low_score=skipped_low_score,
            skipped_ambiguous=skipped_ambiguous,
            mapped_candidate_ids=mapped_candidate_ids,
        )

    def generate_pdf_mapping_drafts(
        self,
        *,
        scan_id: str,
        min_score: int = 70,
        include_already_mapped: bool = False,
        limit_candidates: int = 250,
        provider: str = "heuristic",
        overwrite_existing: bool = True,
        genie_batch_size: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        genie_call_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> GeneratePdfMappingDraftsResult:
        if provider not in {"heuristic", "genie_api"}:
            raise ValueError("provider must be one of: heuristic, genie_api")
        if provider == "genie_api" and not self._genie_mapping_client.configured:
            raise ValueError(
                "Genie provider requires DATABRICKS_HOST, DATABRICKS_GENIE_SPACE_ID, and auth via DATABRICKS_TOKEN/OPENAI_API_KEY or DATABRICKS_CLIENT_ID+DATABRICKS_CLIENT_SECRET"
            )
        min_score = max(0, min(100, min_score))
        limit_candidates = max(1, min(2000, limit_candidates))
        candidates = self.list_pdf_candidates(scan_id)[:limit_candidates]
        masters = self.list_master_data_points()
        aliases_by_master = self._aliases_by_data_point_id()

        if overwrite_existing:
            self._session.query(PdfMappingDraft).filter(PdfMappingDraft.scan_id == scan_id).delete()

        drafted_count = 0
        skipped_count = 0
        skipped_already_mapped = 0
        skipped_low_score = 0
        drafts: list[PdfMappingDraftSuggestion] = []
        now = _utc_now()
        scored_by_candidate_id: dict[str, list[MasterMappingSuggestion]] = {}
        candidates_by_id: dict[str, SurveyPdfDataPointCandidate] = {}

        for candidate in candidates:
            if candidate.master_data_point_id and not include_already_mapped:
                skipped_already_mapped += 1
                skipped_count += 1
                continue

            scored: list[MasterMappingSuggestion] = []
            for master in masters:
                suggestion = _score_candidate_against_master(
                    candidate,
                    master,
                    aliases_by_master.get(master.data_point_id, []),
                    self._mapping_similarity_scorer,
                )
                if suggestion:
                    scored.append(suggestion)
            scored.sort(key=lambda item: (-item.score, item.data_point_id))
            if not scored:
                skipped_low_score += 1
                skipped_count += 1
                continue
            scored_by_candidate_id[candidate.candidate_id] = scored
            candidates_by_id[candidate.candidate_id] = candidate

        genie_choices: dict[str, GenieMappingChoice] = {}
        if provider == "genie_api" and scored_by_candidate_id:
            configured_batch_size = genie_batch_size if genie_batch_size is not None else self._settings.databricks_genie_batch_size
            batch_size = max(1, min(200, configured_batch_size))
            max_prompt_chars = max(1000, min(24000, self._settings.databricks_genie_max_prompt_chars))
            option_limit = max(1, min(12, self._settings.databricks_genie_options_per_candidate))
            candidate_ids = list(scored_by_candidate_id.keys())
            batch_items = [
                {
                    "candidate": candidates_by_id[candidate_id],
                    "scored": scored_by_candidate_id[candidate_id][:option_limit],
                }
                for candidate_id in candidate_ids
            ]
            batches = self._split_genie_batches(
                candidates=batch_items,
                max_candidates_per_batch=batch_size,
                max_prompt_chars=max_prompt_chars,
            )
            total_batches = len(batches)
            for batch_index, batch_payload in enumerate(batches, start=1):
                batch_ids = [item["candidate"].candidate_id for item in batch_payload]
                estimated_prompt_chars = self._estimate_genie_batch_prompt_chars(batch_payload)
                try:
                    batch_choices, request_payload, client_trace = self._choose_with_genie_batch(candidates=batch_payload)
                except Exception as exc:
                    if genie_call_callback:
                        genie_call_callback(
                            {
                                "batch_index": batch_index,
                                "total_batches": total_batches,
                                "provider": provider,
                                "status": "failed",
                                "request_payload": self._build_genie_batch_payload(batch_payload),
                                "response_payload": None,
                                "client_trace": self._genie_client_trace(),
                                "error": str(exc),
                            }
                        )
                    raise
                genie_choices.update(batch_choices)
                if genie_call_callback:
                    genie_call_callback(
                        {
                            "batch_index": batch_index,
                            "total_batches": total_batches,
                            "provider": provider,
                            "status": "completed",
                            "request_payload": request_payload,
                            "response_payload": self._serialize_genie_choices(batch_choices),
                            "client_trace": client_trace,
                            "error": None,
                        }
                    )
                if progress_callback:
                    response_data = []
                    for candidate_id in batch_ids:
                        choice = batch_choices.get(candidate_id)
                        if not choice:
                            continue
                        response_data.append(
                            {
                                "candidate_id": candidate_id,
                                "master_data_point_id": choice.master_data_point_id,
                                "confidence": choice.confidence,
                                "field_key": choice.field_key,
                                "reason": choice.reason,
                            }
                        )
                    progress_callback(
                        {
                            "type": "genie_batch",
                            "status": "completed",
                            "completed_batches": batch_index,
                            "total_batches": total_batches,
                            "batch_candidate_count": len(batch_ids),
                            "requested_batch_size": batch_size,
                            "estimated_prompt_chars": estimated_prompt_chars,
                            "max_prompt_chars": max_prompt_chars,
                            "provider": provider,
                            "response_data": response_data,
                        }
                    )

        for candidate_id, scored in scored_by_candidate_id.items():
            candidate = candidates_by_id[candidate_id]
            top = scored[0]
            selected = top
            mapped_field_key: str | None = None
            reason_suffix = ""

            if provider == "genie_api":
                genie_choice = genie_choices.get(candidate_id)
                if genie_choice:
                    chosen = next((item for item in scored if item.data_point_id == (genie_choice.master_data_point_id or "")), None)
                    if chosen:
                        selected = chosen
                    if genie_choice.confidence is not None:
                        selected = MasterMappingSuggestion(
                            data_point_id=selected.data_point_id,
                            canonical_name=selected.canonical_name,
                            semantic_key=selected.semantic_key,
                            score=max(0, min(100, genie_choice.confidence)),
                            reason=selected.reason,
                        )
                    mapped_field_key = genie_choice.field_key
                    if genie_choice.reason:
                        reason_suffix = f"genie: {genie_choice.reason}"

            if selected.score < min_score:
                skipped_low_score += 1
                skipped_count += 1
                continue

            matched_master = next((m for m in masters if m.data_point_id == selected.data_point_id), None)
            if not matched_master:
                skipped_count += 1
                continue
            if mapped_field_key:
                view, value_column, year_column, transform_json = _build_binding_from_field_key(mapped_field_key)
            else:
                view, value_column, year_column, transform_json = _build_draft_binding_for_candidate(
                    candidate,
                    selected,
                    matched_master,
                )
            reason = selected.reason
            if reason_suffix:
                reason = f"{reason}; {reason_suffix}"
            if not view and not value_column:
                reason = f"{reason}; no databricks binding inferred"
            draft = PdfMappingDraft(
                draft_id=f"pdfdraft_{uuid.uuid4().hex[:12]}",
                scan_id=scan_id,
                candidate_id=candidate.candidate_id,
                survey_id=candidate.survey_id,
                provider=provider,
                status="PENDING_REVIEW",
                score=selected.score,
                master_data_point_id=selected.data_point_id,
                databricks_view=view,
                databricks_value_column=value_column,
                databricks_year_column=year_column,
                transform_json=transform_json,
                reason=reason,
                created_at=now,
                updated_at=now,
            )
            self._session.add(draft)
            drafted_count += 1
            drafts.append(_draft_to_suggestion(draft, field_name=candidate.field_name, label_text=candidate.label_text))

        self._session.commit()
        drafts.sort(key=lambda item: (-item.score, item.candidate_id))
        return GeneratePdfMappingDraftsResult(
            scan_id=scan_id,
            total_candidates=len(candidates),
            drafted_count=drafted_count,
            skipped_count=skipped_count,
            skipped_already_mapped=skipped_already_mapped,
            skipped_low_score=skipped_low_score,
            drafts=drafts,
        )

    def list_pdf_mapping_drafts(
        self,
        *,
        scan_id: str,
        status: str | None = None,
        limit: int = 250,
    ) -> list[PdfMappingDraftSuggestion]:
        self.get_pdf_scan(scan_id)
        limit = max(1, min(2000, limit))
        query = select(PdfMappingDraft).where(PdfMappingDraft.scan_id == scan_id)
        if status:
            query = query.where(PdfMappingDraft.status == status.strip().upper())
        query = query.order_by(PdfMappingDraft.score.desc(), PdfMappingDraft.created_at.desc()).limit(limit)
        rows = list(self._session.execute(query).scalars())
        candidates = {
            row.candidate_id: row
            for row in self._session.execute(
                select(SurveyPdfDataPointCandidate).where(SurveyPdfDataPointCandidate.scan_id == scan_id)
            ).scalars()
        }
        result: list[PdfMappingDraftSuggestion] = []
        for row in rows:
            candidate = candidates.get(row.candidate_id)
            field_name = candidate.field_name if candidate else ""
            label_text = candidate.label_text if candidate else ""
            result.append(_draft_to_suggestion(row, field_name=field_name, label_text=label_text))
        return result

    def approve_pdf_mapping_draft(
        self,
        *,
        draft_id: str,
        apply_binding: bool = True,
        overwrite_master_binding: bool = False,
    ) -> ApprovePdfMappingDraftResult:
        draft = self._session.get(PdfMappingDraft, draft_id)
        if not draft:
            raise ValueError(f"Unknown draft_id: {draft_id}")
        candidate = self._session.get(SurveyPdfDataPointCandidate, draft.candidate_id)
        if not candidate:
            raise ValueError(f"Unknown candidate_id for draft: {draft.candidate_id}")
        master = self._session.get(MasterDataPoint, draft.master_data_point_id)
        if not master:
            raise ValueError(f"Unknown master_data_point_id for draft: {draft.master_data_point_id}")
        if draft.status == "APPROVED":
            raise ValueError(f"Draft already approved: {draft_id}")
        if draft.status == "REJECTED":
            raise ValueError(f"Draft already rejected: {draft_id}")

        binding_applied = False
        if apply_binding:
            should_apply = overwrite_master_binding or not (
                master.databricks_view or master.databricks_value_column or master.databricks_year_column
            )
            if should_apply:
                master.databricks_view = draft.databricks_view.strip()
                master.databricks_value_column = draft.databricks_value_column.strip()
                master.databricks_year_column = draft.databricks_year_column.strip()
                master.transform_json = draft.transform_json.strip() or "{}"
                master.updated_at = _utc_now()
                binding_applied = True

        candidate.master_data_point_id = draft.master_data_point_id
        candidate.status = "MAPPED"
        candidate.updated_at = _utc_now()

        draft.status = "APPROVED"
        draft.updated_at = _utc_now()
        self._session.commit()
        return ApprovePdfMappingDraftResult(
            draft=_draft_to_suggestion(draft, field_name=candidate.field_name, label_text=candidate.label_text),
            candidate_id=candidate.candidate_id,
            master_data_point_id=draft.master_data_point_id,
            binding_applied=binding_applied,
        )
    def resolve_mapped_pdf_scan(
        self,
        *,
        scan_id: str,
        survey_year: int,
        settings: Settings | None = None,
        sql_reader: object | None = None,
    ) -> "ResolvedPdfScanPayload":
        candidates = self.list_pdf_candidates(scan_id)
        mapped_candidates = [candidate for candidate in candidates if candidate.master_data_point_id]
        master_ids = sorted({candidate.master_data_point_id for candidate in mapped_candidates})
        masters = {
            row.data_point_id: row
            for row in self._session.execute(
                select(MasterDataPoint).where(MasterDataPoint.data_point_id.in_(master_ids))
            ).scalars()
        }
        catalog_rows = [
            SurveyFieldCatalog(
                field_id=master.data_point_id,
                section_id="pdf_master",
                label_text=master.canonical_name,
                input_kind="text",
                required_flag=False,
                databricks_view=master.databricks_view,
                databricks_value_column=master.databricks_value_column,
                databricks_year_column=master.databricks_year_column,
                transform_json=master.transform_json,
                status=master.status,
            )
            for master in masters.values()
            if master.status == "ACTIVE"
        ]
        resolver = DatabricksFieldResolver(settings=settings or self._settings_for_pdf_resolution(), sql_reader=sql_reader)  # type: ignore[arg-type]
        resolved = resolver.resolve_section_payload(
            section_id="pdf_master",
            survey_year=survey_year,
            catalog_rows=catalog_rows,
        )
        values: dict[str, dict[str, str]] = {}
        missing_candidates: list[str] = []
        unmapped_candidates: list[str] = []
        for candidate in candidates:
            if not candidate.master_data_point_id:
                unmapped_candidates.append(candidate.candidate_id)
                continue
            master = masters.get(candidate.master_data_point_id)
            if not master or candidate.master_data_point_id in resolved.missing_fields:
                missing_candidates.append(candidate.candidate_id)
                continue
            values[candidate.candidate_id] = {
                "value": resolved.values[candidate.master_data_point_id],
                "field_name": candidate.field_name,
                "label_text": candidate.label_text,
                "master_data_point_id": candidate.master_data_point_id,
                "canonical_name": master.canonical_name,
                "semantic_key": master.semantic_key,
                "databricks_view": master.databricks_view,
            }
        return ResolvedPdfScanPayload(
            scan_id=scan_id,
            survey_year=survey_year,
            values=values,
            missing_candidates=missing_candidates,
            unmapped_candidates=unmapped_candidates,
        )

    def publish_pdf_scan_to_field_catalog(
        self,
        *,
        scan_id: str,
        section_id: str | None = None,
        overwrite: bool = True,
    ) -> list[SurveyFieldCatalog]:
        scan = self.get_pdf_scan(scan_id)
        candidates = [candidate for candidate in self.list_pdf_candidates(scan_id) if candidate.master_data_point_id]
        if not candidates:
            raise ValueError(f"No mapped candidates found for scan_id: {scan_id}")
        target_section_id = (section_id or f"pdf_{scan.survey_id}").strip()
        if not target_section_id:
            raise ValueError("section_id cannot be empty")

        master_ids = sorted({candidate.master_data_point_id for candidate in candidates})
        masters = {
            row.data_point_id: row
            for row in self._session.execute(
                select(MasterDataPoint).where(MasterDataPoint.data_point_id.in_(master_ids))
            ).scalars()
        }

        rows: list[SurveyFieldCatalog] = []
        for candidate in candidates:
            master = masters.get(candidate.master_data_point_id)
            if not master:
                continue
            field_id = _field_catalog_id_for_pdf_candidate(scan.survey_id, candidate.candidate_key)
            row = self._session.get(SurveyFieldCatalog, field_id)
            if row and not overwrite:
                rows.append(row)
                continue
            if not row:
                row = SurveyFieldCatalog(
                    field_id=field_id,
                    section_id=target_section_id,
                    label_text=candidate.label_text,
                    input_kind=_catalog_input_kind(candidate.input_kind),
                    required_flag=False,
                    databricks_view=master.databricks_view,
                    databricks_value_column=master.databricks_value_column,
                    databricks_year_column=master.databricks_year_column,
                    transform_json=master.transform_json,
                    status="ACTIVE",
                    created_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                self._session.add(row)
            else:
                row.section_id = target_section_id
                row.label_text = candidate.label_text
                row.input_kind = _catalog_input_kind(candidate.input_kind)
                row.databricks_view = master.databricks_view
                row.databricks_value_column = master.databricks_value_column
                row.databricks_year_column = master.databricks_year_column
                row.transform_json = master.transform_json
                row.status = "ACTIVE"
                row.updated_at = _utc_now()
            rows.append(row)
        self._session.commit()
        return rows

    def _aliases_by_data_point_id(self) -> dict[str, list[MasterDataPointAlias]]:
        aliases: dict[str, list[MasterDataPointAlias]] = {}
        for row in self._session.execute(select(MasterDataPointAlias)).scalars():
            aliases.setdefault(row.data_point_id, []).append(row)
        return aliases

    def _ensure_master_alias(
        self,
        *,
        data_point_id: str,
        alias_text: str,
        source: str,
        aliases_by_master: dict[str, list[MasterDataPointAlias]],
    ) -> int:
        normalized = _normalize_label(alias_text)
        if not normalized or normalized in {"undefined", "unknown", "other", "yes", "no"}:
            return 0
        existing = aliases_by_master.get(data_point_id, [])
        if any((alias.normalized_alias or _normalize_label(alias.alias_text)) == normalized for alias in existing):
            return 0
        alias = MasterDataPointAlias(
            alias_id=f"alias_{uuid.uuid4().hex[:12]}",
            data_point_id=data_point_id,
            alias_text=alias_text.strip(),
            normalized_alias=normalized,
            source=source,
            created_at=_utc_now(),
        )
        self._session.add(alias)
        existing.append(alias)
        aliases_by_master[data_point_id] = existing
        return 1

    def _settings_for_pdf_resolution(self) -> Settings:
        return cast(Settings, self._settings)

    def _split_genie_batches(
        self,
        *,
        candidates: list[dict[str, Any]],
        max_candidates_per_batch: int,
        max_prompt_chars: int,
    ) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for item in candidates:
            proposed = [*current, item]
            exceeds_count = len(proposed) > max_candidates_per_batch
            exceeds_size = self._estimate_genie_batch_prompt_chars(proposed) > max_prompt_chars
            if current and (exceeds_count or exceeds_size):
                batches.append(current)
                current = [item]
            else:
                current = proposed
        if current:
            batches.append(current)
        return batches

    def _estimate_genie_batch_prompt_chars(self, candidates: list[dict[str, Any]]) -> int:
        prompt_overhead_chars = 2000
        payload = self._build_genie_batch_payload(candidates)
        return prompt_overhead_chars + len(json.dumps(payload, ensure_ascii=True))

    def _build_genie_batch_payload(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        all_master_ids: set[str] = set()
        for item in candidates:
            scored = item["scored"]
            all_master_ids.update(s.data_point_id for s in scored)
        masters_by_id = {
            row.data_point_id: row
            for row in self._session.execute(
                select(MasterDataPoint).where(MasterDataPoint.data_point_id.in_(sorted(all_master_ids)))
            ).scalars()
        }
        payload: list[dict[str, Any]] = []
        for item in candidates:
            candidate = item["candidate"]
            scored = item["scored"]
            options: list[dict[str, Any]] = []
            for score_item in scored:
                master = masters_by_id.get(score_item.data_point_id)
                options.append(
                    {
                        "master_data_point_id": score_item.data_point_id,
                        "canonical_name": score_item.canonical_name,
                        "semantic_key": score_item.semantic_key,
                        "heuristic_score": score_item.score,
                        "databricks_view": master.databricks_view if master else "",
                    }
                )
            payload.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "field_name": candidate.field_name,
                    "label_text": candidate.label_text,
                    "label_source": candidate.label_source,
                    "datapoint_intent": candidate.datapoint_intent,
                    "nearby_text": candidate.nearby_text,
                    "options": options,
                }
            )
        return payload

    def _choose_with_genie_batch(
        self,
        *,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, GenieMappingChoice], list[dict[str, Any]], dict[str, Any] | None]:
        if not candidates:
            return ({}, [], None)
        payload = self._build_genie_batch_payload(candidates)
        choices = self._genie_mapping_client.choose_many_master_data_points(candidates=payload)
        if not choices:
            raise RuntimeError("Genie API returned no parseable mapping choices")
        return choices, payload, self._genie_client_trace()

    def _genie_client_trace(self) -> dict[str, Any] | None:
        trace_getter = getattr(self._genie_mapping_client, "get_last_batch_trace", None)
        if callable(trace_getter):
            trace = trace_getter()
            if isinstance(trace, dict):
                return trace
        return None

    def _serialize_genie_choices(self, choices: dict[str, GenieMappingChoice]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate_id, choice in choices.items():
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "master_data_point_id": choice.master_data_point_id,
                    "confidence": choice.confidence,
                    "field_key": choice.field_key,
                    "reason": choice.reason,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Genie-first value resolution
    # ------------------------------------------------------------------
    def build_pdf_page_context_cache(
        self,
        *,
        scan_id: str,
        limit_pages: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        scan = self.get_pdf_scan(scan_id)
        candidates = self.list_pdf_candidates(scan_id)
        

        candidates_by_page: dict[int, list[SurveyPdfDataPointCandidate]] = {}
        for candidate in candidates:
            page_number = candidate.page_number or 1
            candidates_by_page.setdefault(page_number, []).append(candidate)

        page_numbers = sorted(candidates_by_page.keys())
        if limit_pages is not None:
            page_numbers = page_numbers[: max(1, int(limit_pages))]

        created_count = 0
        reused_count = 0
        failed_count = 0
        pages: list[dict[str, Any]] = []
        errors: list[str] = []

        for page_number in page_numbers:
            try:
                existing = load_page_context(
                    scan_id=scan_id,
                    page_number=page_number,
                    base_dir=self._settings.pdf_upload_dir,
                )

                if existing and not force:
                    reused_count += 1
                    pages.append(
                        {
                            "page_number": page_number,
                            "status": "reused",
                            "image_path": existing.image_path,
                            "context_json_path": existing.context_json_path,
                            "field_count": len(candidates_by_page[page_number]),
                        }
                    )
                    continue

                image_dir, _context_path = page_context_paths(
                    scan_id=scan_id,
                    page_number=page_number,
                    base_dir=self._settings.pdf_upload_dir,
                )

                image_path = render_pdf_page_to_png(
                    pdf_path=scan.file_path,
                    page_number=page_number,
                    output_dir=str(image_dir),
                )

                context = build_basic_page_context_from_candidates(
                    scan_id=scan_id,
                    page_number=page_number,
                    image_path=image_path,
                    candidates=candidates_by_page[page_number],
                    base_dir=self._settings.pdf_upload_dir,
                )

                created_count += 1
                pages.append(
                    {
                        "page_number": page_number,
                        "status": "created",
                        "image_path": context.image_path,
                        "context_json_path": context.context_json_path,
                        "field_count": len(candidates_by_page[page_number]),
                    }
                )

            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                errors.append(f"page {page_number}: {exc}")

        return {
            "scan_id": scan_id,
            "page_count": len(page_numbers),
            "created_count": created_count,
            "reused_count": reused_count,
            "failed_count": failed_count,
            "pages": pages,
            "errors": errors,
        }
    def resolve_pdf_scan_via_genie(
        self,
        *,
        scan_id: str,
        survey_year: int,
        batch_size: int = 50,
        min_confidence: int = 60,
        force_regenie: bool = False,
        page_numbers: list[int] | None = None,
    ) -> dict[str, Any]:
        from apps.api.databricks_genie_client import DatabricksGenieClient, GenieResolution
        from apps.api.databricks_resolver import DatabricksSqlValueReader

        candidates = list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .order_by(SurveyPdfDataPointCandidate.nearby_text, SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )

        if page_numbers:
            allowed_pages = set(page_numbers)
            candidates = [
                candidate
                for candidate in candidates
                if candidate.page_number in allowed_pages
            ]

        registry = CdsQueryRegistry.default()
        registry_groups: dict[Any, list[SurveyPdfDataPointCandidate]] = {}
        genie_candidates_remaining: list[SurveyPdfDataPointCandidate] = []
        for candidate in candidates:
            registry_query = registry.query_for_field(candidate.field_name)
            if registry_query:
                registry_groups.setdefault(registry_query, []).append(candidate)
            elif candidate.label_source == "openai_enriched":
                # Skip already-resolved. For low-confidence: only retry if Genie never produced SQL at all.
                # Re-running Genie on fields where it already generated low-confidence SQL won't improve results.
                if candidate.status != "GENIE_RESOLVED" and not candidate.genie_sql_template:
                    genie_candidates_remaining.append(candidate)

        resolved = low_confidence = failed = skipped = 0
        now = _utc_now()

        if registry_groups:
            reader = DatabricksSqlValueReader(self._settings_for_pdf_resolution())
            if not reader.configured:
                raise RuntimeError("Databricks SQL warehouse not configured: check DATABRICKS_SQL_WAREHOUSE_ID")

            for registry_query, group in registry_groups.items():
                sql_template = registry_query.sql_template
                sql = apply_registry_year(sql_template, survey_year)
                try:
                    columns, rows = reader.query_rows(sql, row_limit=1000)
                except Exception:  # noqa: BLE001
                    failed += len(group)
                    continue

                for row in group:
                    value = registry.extract_value(
                        query=registry_query,
                        field_name=row.field_name,
                        columns=columns,
                        rows=rows,
                    )
                    row.genie_sql_template = sql_template
                    row.genie_table = registry_query.source_table or "cds_query_registry"
                    row.genie_column = row.field_name
                    row.genie_year_column = "cds_registry_params"
                    row.genie_value = value or ""
                    row.genie_confidence = 100 if value else 0
                    row.genie_reason = f"Resolved via CDS registry {registry_query.query_id}"
                    row.genie_resolved_at = now
                    row.updated_at = now
                    if value:
                        row.status = "GENIE_RESOLVED"
                        resolved += 1
                    else:
                        row.status = "GENIE_LOW_CONFIDENCE"
                        low_confidence += 1

        # Group remaining fields by section (extracted from nearby_text prefix "Section: X\n")
        section_groups: dict[str, list[SurveyPdfDataPointCandidate]] = {}
        for c in genie_candidates_remaining:
            section = _extract_section_from_nearby_text(c.nearby_text)
            section_groups.setdefault(section, []).append(c)

        if section_groups:
            genie = DatabricksGenieClient(self._settings_for_pdf_resolution())
            if not genie.configured:
                raise RuntimeError("Genie not configured: check DATABRICKS_HOST and DATABRICKS_GENIE_SPACE_ID")

            for section, group in section_groups.items():
                # Batch by size within each section
                for batch_start in range(0, len(group), batch_size):
                    batch = group[batch_start : batch_start + batch_size]

                    page_context_by_page_number: dict[int, PdfPageContext | None] = {}
                    for c in batch:
                        page_number = c.page_number or 1
                        if page_number not in page_context_by_page_number:
                            page_context_by_page_number[page_number] = load_page_context(
                                scan_id=scan_id,
                                page_number=page_number,
                                base_dir=self._settings.pdf_upload_dir,
                            )

                    genie_payload = [
                        {
                            **_build_genie_candidate_from_pdf_candidate(
                                c,
                                page_context=page_context_by_page_number.get(c.page_number or 1),
                            ),
                            "section": section,
                        }
                        for c in batch
                    ]
                    try:
                        resolutions: dict[str, GenieResolution] = genie.resolve_many_candidates(
                            candidates=genie_payload, survey_year=survey_year
                        )
                    except Exception:  # noqa: BLE001
                        failed += len(batch)
                        continue

                    cand_map = {c.candidate_id: c for c in batch}
                    for cand_id, res in resolutions.items():
                        row = cand_map.get(cand_id)
                        if not row:
                            continue
                        row.genie_sql_template = res.sql_template
                        row.genie_table = res.table
                        row.genie_column = res.column
                        row.genie_year_column = res.year_column
                        row.genie_value = res.value
                        row.genie_confidence = res.confidence
                        row.genie_reason = res.reason
                        row.genie_resolved_at = now
                        row.updated_at = now
                        if res.confidence >= min_confidence and res.value:
                            row.status = "GENIE_RESOLVED"
                            resolved += 1
                        else:
                            row.status = "GENIE_LOW_CONFIDENCE"
                            low_confidence += 1

                    unresolved = [c for c in batch if c.candidate_id not in resolutions]
                    skipped += len(unresolved)

        self._session.commit()
        return {
            "scan_id": scan_id,
            "survey_year": survey_year,
            "resolved": resolved,
            "low_confidence": low_confidence,
            "failed": failed,
            "skipped": skipped,
        }

    def resolve_pdf_scan_direct(
        self,
        *,
        scan_id: str,
        survey_year: int,
    ) -> dict[str, Any]:
        from apps.api.databricks_genie_client import apply_year
        from apps.api.databricks_resolver import DatabricksSqlValueReader

        reader = DatabricksSqlValueReader(self._settings_for_pdf_resolution())
        if not reader.configured:
            raise RuntimeError("Databricks SQL warehouse not configured: check DATABRICKS_SQL_WAREHOUSE_ID")

        candidates = list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .where(SurveyPdfDataPointCandidate.genie_sql_template != "")
            ).scalars()
        )

        refreshed = null_results = sql_errors = needs_regenie = 0
        now = _utc_now()

        for row in candidates:
            sql = apply_year(row.genie_sql_template, survey_year)
            try:
                _cols, rows = reader.query_rows(sql, row_limit=1)
                value = str(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else None
            except Exception:  # noqa: BLE001
                row.direct_sql_failures += 1
                row.updated_at = now
                sql_errors += 1
                if row.direct_sql_failures >= 3:
                    row.status = "NEEDS_REGENIE"
                    needs_regenie += 1
                continue

            if value is None:
                row.direct_sql_failures += 1
                row.updated_at = now
                null_results += 1
                if row.direct_sql_failures >= 3:
                    row.status = "NEEDS_REGENIE"
                    needs_regenie += 1
            else:
                row.genie_value = value
                row.direct_sql_failures = 0
                row.updated_at = now
                refreshed += 1

        self._session.commit()
        return {
            "scan_id": scan_id,
            "survey_year": survey_year,
            "refreshed": refreshed,
            "null_results": null_results,
            "sql_errors": sql_errors,
            "needs_regenie": needs_regenie,
        }

    def list_resolved_values(self, scan_id: str) -> list[SurveyPdfDataPointCandidate]:
        return list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .where(SurveyPdfDataPointCandidate.genie_sql_template != "")
                .order_by(SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )

    def export_resolved_values_to_pdf(
        self,
        *,
        scan_id: str,
        output_file_path: str | None = None,
        flatten: bool = False,
    ) -> FilledPdfExportResult:
        scan = self._session.get(SurveyPdfScan, scan_id)
        if not scan:
            raise ValueError(f"Unknown scan_id: {scan_id}")

        source_path = self._resolve_pdf_source_path(scan.file_path)
        if not source_path:
            raise ValueError(f"Source PDF not found: {scan.file_path}")

        rows = list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .where(SurveyPdfDataPointCandidate.status == "GENIE_RESOLVED")
                .where(SurveyPdfDataPointCandidate.genie_value != "")
                .where(SurveyPdfDataPointCandidate.field_name != "")
                .order_by(SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )
        values_by_field = {row.field_name: row.genie_value for row in rows}

        if output_file_path:
            output_path = Path(output_file_path)
        else:
            export_dir = Path(self._settings.pdf_export_dir)
            output_name = f"{scan.scan_id}_{source_path.stem}_resolved.pdf"
            output_path = export_dir / output_name

        filled_count, missing_pdf_fields = fill_pdf_acroform_fields(
            source_path=source_path,
            values_by_field=values_by_field,
            output_path=output_path,
            flatten=flatten,
        )

        return FilledPdfExportResult(
            scan_id=scan_id,
            source_file_path=str(source_path),
            output_file_path=str(output_path),
            filled_count=filled_count,
            skipped_count=len(missing_pdf_fields),
            missing_pdf_fields=missing_pdf_fields,
        )

    def _resolve_pdf_source_path(self, stored_file_path: str) -> Path | None:
        stored_path = Path(stored_file_path)
        if stored_path.exists() and stored_path.is_file():
            return stored_path

        upload_dir = Path(self._settings.pdf_upload_dir)
        candidates = [upload_dir / stored_path.name]
        parts = stored_path.parts
        if "uploads" in parts:
            uploads_index = len(parts) - 1 - parts[::-1].index("uploads")
            relative_upload_path = Path(*parts[uploads_index + 1 :])
            candidates.insert(0, upload_dir / relative_upload_path)

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None


def fill_pdf_acroform_fields(
    *,
    source_path: Path,
    values_by_field: dict[str, str],
    output_path: Path,
    flatten: bool = False,
) -> tuple[int, list[str]]:
    """Fill AcroForm fields on source_path by field_name, write to output_path.

    Returns (filled_count, missing_pdf_fields), where missing_pdf_fields lists
    field_names in values_by_field that don't exist in the source PDF's AcroForm.
    """
    reader = PdfReader(str(source_path))
    pdf_fields = reader.get_fields() or {}
    available_fields = set(pdf_fields.keys())
    fill_values = {
        field_name: value
        for field_name, value in values_by_field.items()
        if field_name in available_fields
    }
    missing_pdf_fields = sorted(field_name for field_name in values_by_field if field_name not in available_fields)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()
    writer.append(reader)
    if hasattr(writer, "set_need_appearances_writer"):
        writer.set_need_appearances_writer(True)
    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            fill_values,
            auto_regenerate=True,
            flatten=flatten,
        )
    with output_path.open("wb") as handle:
        writer.write(handle)

    return len(fill_values), missing_pdf_fields


def _extract_section_from_nearby_text(nearby_text: str) -> str:
    """Extract section name from enriched nearby_text (format: '... | Section: X | ...')."""
    match = re.search(r"Section:\s*(.+?)(?:\s*\||\n|$)", nearby_text or "")
    return match.group(1).strip() if match else ""


class Slice1Service:
    def __init__(
        self,
        session: Session,
        skyvern_client: SkyvernClient,
        webhook_callback_url: str,
        settings: Settings,
    ) -> None:
        self._session = session
        self._skyvern_client = skyvern_client
        self._webhook_callback_url = webhook_callback_url
        self._settings = settings
        self._resolver = DatabricksFieldResolver(settings=settings)

    def create_run(self, run_id: str, survey_id: str, survey_year: int) -> Run:
        existing = self._session.get(Run, run_id)
        if existing:
            return existing
        run = Run(run_id=run_id, survey_id=survey_id, survey_year=survey_year)
        self._session.add(run)
        self._session.commit()
        return run

    def dispatch_section_validate_activity(
        self,
        run_id: str,
        section_id: str,
        portal_url: str,
        stage: str = "dispatch_validate",
        create_missing_reviews: bool = True,
        browser_session_id: str | None = None,
    ) -> list[SkyvernTask]:
        run = self._session.get(Run, run_id)
        if not run:
            raise ValueError(f"Unknown run_id: {run_id}")

        catalog_rows = self._get_active_catalog_rows(section_id)
        if not catalog_rows:
            catalog_rows = self.bootstrap_section_catalog(section_id)

        payload = self.prepare_section_payload_activity(
            run_id=run_id,
            section_id=section_id,
            survey_year=run.survey_year,
            create_missing_reviews=create_missing_reviews,
        )
        active_fields = self._catalog_rows_to_fields(catalog_rows)
        fields_to_dispatch = [field for field in active_fields if field.field_id in payload.values]
        if not fields_to_dispatch:
            raise ValueError("No resolved fields available to dispatch")

        max_per_task = self._settings.skyvern_max_fields_per_task
        chunks = split_fields_for_task(fields_to_dispatch, max_fields_per_task=max_per_task)
        chunk_total = len(chunks)
        if chunk_total > 1:
            self._add_event(
                run_id,
                "TASK_SPLIT_APPLIED",
                {
                    "section_id": section_id,
                    "chunk_total": chunk_total,
                    "max_fields_per_task": max_per_task,
                },
            )

        created_tasks: list[SkyvernTask] = []
        for chunk_index, chunk_fields in enumerate(chunks):
            spec = build_validate_task(
                run_id=run_id,
                section_id=section_id,
                portal_url=portal_url,
                webhook_callback_url=self._webhook_callback_url,
                fields=chunk_fields,
                expected_values=payload.values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            complexity = len(spec.user_prompt) + len(json.dumps(spec.extraction_schema))
            if complexity > self._settings.skyvern_task_complexity_budget_chars and len(chunk_fields) > 1:
                self._add_event(
                    run_id,
                    "TASK_SPLIT_BUDGET_EXCEEDED",
                    {
                        "section_id": section_id,
                        "chunk_index": chunk_index,
                        "complexity": complexity,
                        "budget": self._settings.skyvern_task_complexity_budget_chars,
                    },
                )
            workflow = self._skyvern_client.create_validate_workflow(
                user_prompt=spec.user_prompt,
                extracted_information_schema=spec.extraction_schema,
                max_steps=self._settings.skyvern_validate_max_steps,
                browser_session_id=browser_session_id,
            )
            task = self._create_skyvern_task_row(
                run_id=run_id,
                section_id=section_id,
                purpose="validate",
                stage=stage,
                workflow_id=workflow.workflow_id,
                request_json={**workflow.raw_response, "url": portal_url, "browser_session_id": browser_session_id},
                expected_values=spec.expected_values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            created_tasks.append(task)
            self._add_event(
                run_id,
                "TASK_SPLIT_CHUNK_DISPATCHED",
                {
                    "task_id": task.task_id,
                    "workflow_id": task.workflow_id,
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "purpose": "validate",
                    "stage": stage,
                },
            )

        self._add_event(
            run_id,
            "VALIDATE_STAGE_DISPATCHED" if stage == "dispatch_validate" else "POST_FILL_VALIDATE_STAGE_DISPATCHED",
            {"section_id": section_id, "stage": stage, "dispatched_tasks": len(created_tasks)},
        )
        self._session.commit()
        return created_tasks

    def dispatch_section_fill_activity(
        self,
        run_id: str,
        section_id: str,
        portal_url: str,
        browser_session_id: str | None = None,
        scan_id: str | None = None,
    ) -> list[SkyvernTask]:
        run = self._session.get(Run, run_id)
        if not run:
            raise ValueError(f"Unknown run_id: {run_id}")

        if scan_id:
            fields_to_dispatch, expected_values = self._pdf_genie_fields_and_values(scan_id)
            self._add_event(
                run_id,
                "PDF_GENIE_PAYLOAD_PREPARED",
                {
                    "scan_id": scan_id,
                    "section_id": section_id,
                    "resolved_fields": len(expected_values),
                },
            )
        else:
            catalog_rows = self._get_active_catalog_rows(section_id)
            if not catalog_rows:
                catalog_rows = self.bootstrap_section_catalog(section_id)

            payload = self.prepare_section_payload_activity(
                run_id=run_id,
                section_id=section_id,
                survey_year=run.survey_year,
                create_missing_reviews=True,
            )
            active_fields = self._catalog_rows_to_fields(catalog_rows)
            fields_to_dispatch = [field for field in active_fields if field.field_id in payload.values]
            expected_values = payload.values

        if not fields_to_dispatch:
            raise ValueError("No resolved fields available to fill")

        max_per_task = self._settings.skyvern_max_fields_per_task
        chunks = split_fields_for_task(fields_to_dispatch, max_fields_per_task=max_per_task)
        chunk_total = len(chunks)
        if chunk_total > 1:
            self._add_event(
                run_id,
                "FILL_TASK_SPLIT_APPLIED",
                {
                    "section_id": section_id,
                    "chunk_total": chunk_total,
                    "max_fields_per_task": max_per_task,
                },
            )

        created_tasks: list[SkyvernTask] = []
        for chunk_index, chunk_fields in enumerate(chunks):
            spec = build_fill_task(
                run_id=run_id,
                section_id=section_id,
                portal_url=portal_url,
                webhook_callback_url=self._webhook_callback_url,
                fields=chunk_fields,
                expected_values=expected_values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            workflow = self._skyvern_client.create_fill_workflow(
                user_prompt=spec.user_prompt,
                extracted_information_schema=spec.extraction_schema,
                max_steps=self._settings.skyvern_fill_max_steps,
                browser_session_id=browser_session_id,
            )
            task = self._create_skyvern_task_row(
                run_id=run_id,
                section_id=section_id,
                purpose="fill",
                stage="draft_fill",
                workflow_id=workflow.workflow_id,
                request_json={
                    **workflow.raw_response,
                    "url": portal_url,
                    "browser_session_id": browser_session_id,
                    "scan_id": scan_id,
                },
                expected_values=spec.expected_values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            created_tasks.append(task)
            self._add_event(
                run_id,
                "FILL_TASK_CHUNK_DISPATCHED",
                {
                    "task_id": task.task_id,
                    "workflow_id": task.workflow_id,
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "purpose": "fill",
                    "stage": "draft_fill",
                },
            )

        self._add_event(
            run_id,
            "FILL_STAGE_DISPATCHED",
            {
                "section_id": section_id,
                "scan_id": scan_id,
                "dispatched_tasks": len(created_tasks),
                "submit_enabled": False,
            },
        )
        self._session.commit()
        return created_tasks

    def dispatch_scan_fields_activity(self, run_id: str, section_id: str, portal_url: str, browser_session_id: str | None = None) -> SkyvernTask:
        run = self._session.get(Run, run_id)
        if not run:
            raise ValueError(f"Unknown run_id: {run_id}")

        spec = build_scan_fields_task(
            run_id=run_id,
            section_id=section_id,
            portal_url=portal_url,
            webhook_callback_url=self._webhook_callback_url,
        )
        workflow = self._skyvern_client.create_scan_workflow(
            user_prompt=spec.user_prompt,
            extracted_information_schema=spec.extraction_schema,
            max_steps=self._settings.skyvern_scan_max_steps,
            browser_session_id=browser_session_id,
        )
        task = self._create_skyvern_task_row(
            run_id=run_id,
            section_id=section_id,
            purpose="scan_fields",
            stage="scan_section_fields",
            workflow_id=workflow.workflow_id,
            request_json={**workflow.raw_response, "url": portal_url},
            expected_values={},
            chunk_index=0,
            chunk_total=1,
        )
        self._add_event(
            run_id,
            "SCAN_FIELDS_TASK_DISPATCHED",
            {"task_id": task.task_id, "workflow_id": task.workflow_id, "section_id": section_id},
        )
        self._session.commit()
        return task

    def _dispatch_pdf_scan_validate_activity(
        self,
        *,
        run_id: str,
        section_id: str,
        portal_url: str,
        scan_id: str,
        stage: str,
        browser_session_id: str | None = None,
    ) -> list[SkyvernTask]:
        if not self._session.get(Run, run_id):
            raise ValueError(f"Unknown run_id: {run_id}")

        fields_to_dispatch, expected_values = self._pdf_genie_fields_and_values(scan_id)
        if not fields_to_dispatch:
            raise ValueError("No resolved PDF fields available to validate")

        max_per_task = self._settings.skyvern_max_fields_per_task
        chunks = split_fields_for_task(fields_to_dispatch, max_fields_per_task=max_per_task)
        chunk_total = len(chunks)

        created_tasks: list[SkyvernTask] = []
        for chunk_index, chunk_fields in enumerate(chunks):
            spec = build_validate_task(
                run_id=run_id,
                section_id=section_id,
                portal_url=portal_url,
                webhook_callback_url=self._webhook_callback_url,
                fields=chunk_fields,
                expected_values=expected_values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            workflow = self._skyvern_client.create_validate_workflow(
                user_prompt=spec.user_prompt,
                extracted_information_schema=spec.extraction_schema,
                max_steps=self._settings.skyvern_validate_max_steps,
                browser_session_id=browser_session_id,
            )
            task = self._create_skyvern_task_row(
                run_id=run_id,
                section_id=section_id,
                purpose="validate",
                stage=stage,
                workflow_id=workflow.workflow_id,
                request_json={
                    **workflow.raw_response,
                    "url": portal_url,
                    "browser_session_id": browser_session_id,
                    "scan_id": scan_id,
                },
                expected_values=spec.expected_values,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            created_tasks.append(task)
            self._add_event(
                run_id,
                "PDF_VALIDATE_TASK_CHUNK_DISPATCHED",
                {
                    "task_id": task.task_id,
                    "workflow_id": task.workflow_id,
                    "chunk_index": chunk_index,
                    "chunk_total": chunk_total,
                    "purpose": "validate",
                    "stage": stage,
                    "scan_id": scan_id,
                },
            )

        self._add_event(
            run_id,
            "POST_FILL_VALIDATE_STAGE_DISPATCHED",
            {
                "section_id": section_id,
                "stage": stage,
                "scan_id": scan_id,
                "dispatched_tasks": len(created_tasks),
            },
        )
        self._session.commit()
        return created_tasks

    def process_skyvern_webhook(self, payload: dict[str, object]) -> tuple[SkyvernTask, int]:
        workflow_id = str(payload.get("workflow_id") or payload.get("id") or "").strip()
        if not workflow_id:
            raise ValueError("Webhook payload missing workflow_id")

        task = self._session.execute(select(SkyvernTask).where(SkyvernTask.workflow_id == workflow_id)).scalar_one_or_none()
        if not task:
            raise ValueError(f"No skyvern task found for workflow_id={workflow_id}")

        status = str(payload.get("status") or payload.get("workflow_status") or "UNKNOWN").upper()
        failure_reason = str(payload.get("failure_reason") or payload.get("error_message") or "")
        extracted_data = payload.get("extracted_information") or payload.get("extracted_data") or {}
        if not isinstance(extracted_data, dict):
            extracted_data = {}
        screenshot_url = str(payload.get("screenshot_url") or "")

        task.status = status
        task.extracted_data_json = json.dumps(extracted_data)
        task.screenshot_url = screenshot_url
        task.updated_at = _utc_now()

        self._add_event(
            task.run_id,
            "SKYVERN_WEBHOOK_RECEIVED",
            {
                "workflow_id": workflow_id,
                "status": status,
                "failure_reason": failure_reason,
                "payload_keys": sorted(payload.keys()),
            },
        )
        if "maximum of 50 planning iterations" in failure_reason.lower():
            self._add_event(
                task.run_id,
                "SKYVERN_ITERATION_LIMIT",
                {"task_id": task.task_id, "workflow_id": workflow_id, "failure_reason": failure_reason},
            )

        mismatch_count = 0
        if task.purpose == "validate":
            mismatch_count = self._create_mismatch_review_items(task=task, extracted_data=extracted_data, screenshot_url=screenshot_url)
        if task.purpose == "scan_fields":
            self._persist_field_discovery_drafts(task=task, extracted_data=extracted_data, screenshot_url=screenshot_url)
        if task.purpose == "fill":
            self._record_fill_completion(task=task, extracted_data=extracted_data)
            self._dispatch_post_fill_validate_if_ready(task=task)
        self._session.commit()
        return task, mismatch_count

    def list_review_items(self, run_id: str) -> list[ReviewItem]:
        return list(self._session.execute(select(ReviewItem).where(ReviewItem.run_id == run_id)).scalars())

    def list_run_events(self, run_id: str) -> list[RunEvent]:
        return list(self._session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.event_id)).scalars())

    def record_event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        self._add_event(run_id, event_type, payload)
        self._session.commit()

    def list_field_discovery_drafts(self, run_id: str, section_id: str | None = None) -> list[FieldDiscoveryDraft]:
        query = select(FieldDiscoveryDraft).where(FieldDiscoveryDraft.run_id == run_id)
        if section_id:
            query = query.where(FieldDiscoveryDraft.section_id == section_id)
        query = query.order_by(FieldDiscoveryDraft.created_at)
        return list(self._session.execute(query).scalars())

    def list_field_catalog(self, section_id: str) -> list[SurveyFieldCatalog]:
        return self._get_active_catalog_rows(section_id)

    def approve_field_discovery_draft(
        self,
        draft_id: str,
        field_id_override: str | None,
        databricks_view: str,
        databricks_value_column: str,
        databricks_year_column: str,
    ) -> SurveyFieldCatalog:
        draft = self._session.get(FieldDiscoveryDraft, draft_id)
        if not draft:
            raise ValueError(f"Unknown draft_id: {draft_id}")

        canonical_field_id = (field_id_override or draft.candidate_field_id).strip()
        if not canonical_field_id:
            raise ValueError("field_id_override or candidate_field_id must be provided")

        catalog = self._session.get(SurveyFieldCatalog, canonical_field_id)
        if not catalog:
            catalog = SurveyFieldCatalog(
                field_id=canonical_field_id,
                section_id=draft.section_id,
                label_text=draft.label_text,
                input_kind=draft.input_kind,
                required_flag=draft.required_flag,
                databricks_view=databricks_view,
                databricks_value_column=databricks_value_column,
                databricks_year_column=databricks_year_column,
                transform_json="{}",
                status="ACTIVE",
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            self._session.add(catalog)
        else:
            catalog.label_text = draft.label_text
            catalog.input_kind = draft.input_kind
            catalog.required_flag = draft.required_flag
            catalog.databricks_view = databricks_view
            catalog.databricks_value_column = databricks_value_column
            catalog.databricks_year_column = databricks_year_column
            catalog.updated_at = _utc_now()

        draft.status = "APPROVED"
        draft.updated_at = _utc_now()
        self._add_event(
            draft.run_id,
            "FIELD_DISCOVERY_APPROVED",
            {"draft_id": draft_id, "field_id": canonical_field_id},
        )
        self._session.commit()
        return catalog

    def reject_field_discovery_draft(self, draft_id: str, notes: str) -> FieldDiscoveryDraft:
        draft = self._session.get(FieldDiscoveryDraft, draft_id)
        if not draft:
            raise ValueError(f"Unknown draft_id: {draft_id}")
        draft.status = "REJECTED"
        draft.notes = notes
        draft.updated_at = _utc_now()
        self._add_event(
            draft.run_id,
            "FIELD_DISCOVERY_REJECTED",
            {"draft_id": draft_id, "notes": notes},
        )
        self._session.commit()
        return draft

    def update_field_catalog_binding(
        self,
        field_id: str,
        databricks_view: str,
        databricks_value_column: str,
        databricks_year_column: str,
        transform_json: str,
    ) -> SurveyFieldCatalog:
        catalog = self._session.get(SurveyFieldCatalog, field_id)
        if not catalog:
            raise ValueError(f"Unknown field_id: {field_id}")
        catalog.databricks_view = databricks_view
        catalog.databricks_value_column = databricks_value_column
        catalog.databricks_year_column = databricks_year_column
        catalog.transform_json = transform_json
        catalog.updated_at = _utc_now()
        self._session.commit()
        return catalog

    def bootstrap_section_catalog(self, section_id: str) -> list[SurveyFieldCatalog]:
        defaults = get_section_field_definitions(section_id)
        created: list[SurveyFieldCatalog] = []
        for field in defaults:
            existing = self._session.get(SurveyFieldCatalog, field.field_id)
            if existing:
                created.append(existing)
                continue
            catalog = SurveyFieldCatalog(
                field_id=field.field_id,
                section_id=section_id,
                label_text=field.label_hint,
                input_kind=field.input_kind,
                required_flag=field.required,
                databricks_view=f"survey_{section_id}_view",
                databricks_value_column=field.field_id,
                databricks_year_column="survey_year",
                transform_json="{}",
                status="ACTIVE",
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            self._session.add(catalog)
            created.append(catalog)
        self._session.commit()
        return created

    def prepare_section_payload_activity(
        self,
        run_id: str,
        section_id: str,
        survey_year: int,
        create_missing_reviews: bool,
    ) -> ResolvedSectionPayload:
        catalog_rows = self._get_active_catalog_rows(section_id)
        if not catalog_rows:
            raise ValueError(f"No active catalog rows for section: {section_id}")

        payload = self._resolver.resolve_section_payload(
            section_id=section_id,
            survey_year=survey_year,
            catalog_rows=catalog_rows,
        )
        self._add_event(
            run_id,
            "SECTION_PAYLOAD_PREPARED",
            {
                "section_id": section_id,
                "resolved_fields": len(payload.values),
                "missing_fields": payload.missing_fields,
            },
        )
        if create_missing_reviews:
            for field_id in payload.missing_fields:
                review_item = ReviewItem(
                    review_item_id=f"ri_{uuid.uuid4().hex[:12]}",
                    run_id=run_id,
                    section_id=section_id,
                    field_id=field_id,
                    reason_code="MISSING_IN_DATABRICKS",
                    expected_value="",
                    observed_value="",
                    screenshot_url="",
                    status="OPEN",
                    created_at=_utc_now(),
                )
                self._session.add(review_item)
            if payload.missing_fields:
                self._add_event(
                    run_id,
                    "REVIEW_ITEMS_CREATED",
                    {
                        "count": len(payload.missing_fields),
                        "reason_code": "MISSING_IN_DATABRICKS",
                        "section_id": section_id,
                    },
                )
        self._session.commit()
        return payload

    def execute_section_pipeline(self, run_id: str, section_id: str, portal_url: str, browser_session_id: str | None = None) -> dict[str, object]:
        run = self._session.get(Run, run_id)
        if not run:
            raise ValueError(f"Unknown run_id: {run_id}")
        self._add_event(run_id, "SECTION_PIPELINE_STARTED", {"section_id": section_id, "portal_url": portal_url})
        scan_task = self.dispatch_scan_fields_activity(run_id, section_id, portal_url, browser_session_id=browser_session_id)
        # Validate dispatches after scan so the catalog is ready; scan and validate
        # share the same browser session so the portal stays authenticated.
        validate_tasks = self.dispatch_section_validate_activity(run_id, section_id, portal_url, browser_session_id=browser_session_id)
        resolved_field_ids: set[str] = set()
        for task in validate_tasks:
            expected_values = json.loads(task.expected_values_json)
            if isinstance(expected_values, dict):
                resolved_field_ids.update(str(field_id) for field_id in expected_values)
        self._add_event(
            run_id,
            "SECTION_PIPELINE_DISPATCHED",
            {
                "section_id": section_id,
                "scan_task_id": scan_task.task_id,
                "scan_workflow_id": scan_task.workflow_id,
                "validate_task_ids": [task.task_id for task in validate_tasks],
                "validate_workflow_ids": [task.workflow_id for task in validate_tasks],
                "resolved_field_count": len(resolved_field_ids),
                "browser_session_id": browser_session_id,
            },
        )
        self._session.commit()
        return {
            "scan_task_id": scan_task.task_id,
            "scan_workflow_id": scan_task.workflow_id,
            "validate_task_ids": [task.task_id for task in validate_tasks],
            "validate_workflow_ids": [task.workflow_id for task in validate_tasks],
            "resolved_field_count": len(resolved_field_ids),
        }

    def execute_draft_fill_pipeline(
        self,
        run_id: str,
        section_id: str,
        portal_url: str,
        browser_session_id: str | None = None,
        scan_id: str | None = None,
    ) -> dict[str, object]:
        if not self._session.get(Run, run_id):
            raise ValueError(f"Unknown run_id: {run_id}")
        self._add_event(
            run_id,
            "DRAFT_FILL_PIPELINE_STARTED",
            {"section_id": section_id, "portal_url": portal_url, "scan_id": scan_id},
        )
        fill_tasks = self.dispatch_section_fill_activity(
            run_id,
            section_id,
            portal_url,
            browser_session_id=browser_session_id,
            scan_id=scan_id,
        )
        filled_field_ids: set[str] = set()
        for task in fill_tasks:
            expected_values = json.loads(task.expected_values_json)
            if isinstance(expected_values, dict):
                filled_field_ids.update(str(field_id) for field_id in expected_values)
        self._add_event(
            run_id,
            "DRAFT_FILL_PIPELINE_DISPATCHED",
            {
                "section_id": section_id,
                "fill_task_ids": [task.task_id for task in fill_tasks],
                "fill_workflow_ids": [task.workflow_id for task in fill_tasks],
                "field_count": len(filled_field_ids),
                "submit_enabled": False,
                "scan_id": scan_id,
            },
        )
        self._session.commit()
        return {
            "fill_task_ids": [task.task_id for task in fill_tasks],
            "fill_workflow_ids": [task.workflow_id for task in fill_tasks],
            "field_count": len(filled_field_ids),
            "submit_enabled": False,
        }

    def compute_run_metrics(self, run_id: str) -> dict[str, int]:
        tasks = list(self._session.execute(select(SkyvernTask).where(SkyvernTask.run_id == run_id)).scalars())
        discovery = list(
            self._session.execute(select(FieldDiscoveryDraft).where(FieldDiscoveryDraft.run_id == run_id)).scalars()
        )
        approved_discovery_count = sum(1 for draft in discovery if draft.status == "APPROVED")
        split_event_count = self._count_events(run_id, "TASK_SPLIT_APPLIED")
        iteration_failure_count = self._count_events(run_id, "SKYVERN_ITERATION_LIMIT")
        return {
            "tasks_total": len(tasks),
            "tasks_validate": sum(1 for task in tasks if task.purpose == "validate"),
            "tasks_scan_fields": sum(1 for task in tasks if task.purpose == "scan_fields"),
            "tasks_fill": sum(1 for task in tasks if task.purpose == "fill"),
            "split_rate_percent": int((split_event_count / max(1, len(tasks))) * 100),
            "field_discovery_total": len(discovery),
            "field_discovery_approved": approved_discovery_count,
            "iteration_failure_count": iteration_failure_count,
        }

    def _record_fill_completion(self, task: SkyvernTask, extracted_data: dict[str, object]) -> None:
        submit_attempted = str(extracted_data.get("submit_attempted", "false")).strip().lower()
        self._add_event(
            task.run_id,
            "FILL_TASK_COMPLETED",
            {
                "task_id": task.task_id,
                "workflow_id": task.workflow_id,
                "status": task.status,
                "submit_attempted": submit_attempted == "true",
            },
        )
        if submit_attempted == "true":
            self._add_event(
                task.run_id,
                "SUBMIT_GUARD_VIOLATION",
                {"task_id": task.task_id, "workflow_id": task.workflow_id, "section_id": task.section_id},
            )

    def _dispatch_post_fill_validate_if_ready(self, task: SkyvernTask) -> None:
        fill_tasks = list(
            self._session.execute(
                select(SkyvernTask)
                .where(SkyvernTask.run_id == task.run_id)
                .where(SkyvernTask.section_id == task.section_id)
                .where(SkyvernTask.purpose == "fill")
                .where(SkyvernTask.stage == "draft_fill")
            ).scalars()
        )
        if not fill_tasks or any(fill_task.status == "DISPATCHED" for fill_task in fill_tasks):
            return

        existing_post_fill_validate = self._session.execute(
            select(SkyvernTask)
            .where(SkyvernTask.run_id == task.run_id)
            .where(SkyvernTask.section_id == task.section_id)
            .where(SkyvernTask.purpose == "validate")
            .where(SkyvernTask.stage == "post_fill_validate")
        ).first()
        if existing_post_fill_validate:
            return

        portal_url = self._extract_portal_url_from_request(task.request_json)
        if not portal_url:
            portal_url = "http://fake-form"
        browser_session_id = self._extract_browser_session_id_from_request(task.request_json)
        scan_id = self._extract_scan_id_from_request(task.request_json)
        self._add_event(
            task.run_id,
            "POST_FILL_VALIDATE_READY",
            {"section_id": task.section_id, "scan_id": scan_id, "fill_task_count": len(fill_tasks)},
        )
        if scan_id:
            self._dispatch_pdf_scan_validate_activity(
                run_id=task.run_id,
                section_id=task.section_id,
                portal_url=portal_url,
                scan_id=scan_id,
                stage="post_fill_validate",
                browser_session_id=browser_session_id,
            )
            return
        self.dispatch_section_validate_activity(
            run_id=task.run_id,
            section_id=task.section_id,
            portal_url=portal_url,
            stage="post_fill_validate",
            create_missing_reviews=False,
            browser_session_id=browser_session_id,
        )

    def _create_mismatch_review_items(self, task: SkyvernTask, extracted_data: dict[str, object], screenshot_url: str) -> int:
        expected_values = json.loads(task.expected_values_json)
        if not isinstance(expected_values, dict):
            return 0

        mismatches = 0
        for field_id, expected_value in expected_values.items():
            expected_text = str(expected_value).strip()
            observed_text = str(extracted_data.get(field_id, "")).strip()
            if expected_text == observed_text:
                continue
            review_item = ReviewItem(
                review_item_id=f"ri_{uuid.uuid4().hex[:12]}",
                run_id=task.run_id,
                section_id=task.section_id,
                field_id=field_id,
                reason_code="SKYVERN_VALIDATION_MISMATCH",
                expected_value=expected_text,
                observed_value=observed_text,
                screenshot_url=screenshot_url,
                status="OPEN",
                created_at=_utc_now(),
            )
            self._session.add(review_item)
            mismatches += 1

        if mismatches:
            self._add_event(
                task.run_id,
                "REVIEW_ITEMS_CREATED",
                {"task_id": task.task_id, "count": mismatches, "reason_code": "SKYVERN_VALIDATION_MISMATCH"},
            )
        return mismatches

    def _add_event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        event = RunEvent(
            run_id=run_id,
            event_type=event_type,
            payload_json=json.dumps(payload),
            created_at=_utc_now(),
        )
        self._session.add(event)

    def _create_skyvern_task_row(
        self,
        run_id: str,
        section_id: str,
        purpose: str,
        stage: str,
        workflow_id: str,
        request_json: dict[str, object],
        expected_values: dict[str, str],
        chunk_index: int,
        chunk_total: int,
    ) -> SkyvernTask:
        task = SkyvernTask(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            section_id=section_id,
            purpose=purpose,
            stage=stage,
            workflow_id=workflow_id,
            status="DISPATCHED",
            expected_values_json=json.dumps(expected_values),
            request_json=json.dumps(request_json),
            extracted_data_json="{}",
            screenshot_url="",
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        self._session.add(task)
        return task

    def _persist_field_discovery_drafts(
        self,
        task: SkyvernTask,
        extracted_data: dict[str, object],
        screenshot_url: str,
    ) -> None:
        raw_scan_json = extracted_data.get("scan_result_json")
        if not isinstance(raw_scan_json, str):
            self._add_event(
                task.run_id,
                "FIELD_DISCOVERY_PARSE_FAILED",
                {"task_id": task.task_id, "reason": "scan_result_json missing"},
            )
            return

        try:
            parsed = json.loads(raw_scan_json)
        except json.JSONDecodeError:
            self._add_event(
                task.run_id,
                "FIELD_DISCOVERY_PARSE_FAILED",
                {"task_id": task.task_id, "reason": "scan_result_json is invalid JSON"},
            )
            return
        if not isinstance(parsed, list):
            self._add_event(
                task.run_id,
                "FIELD_DISCOVERY_PARSE_FAILED",
                {"task_id": task.task_id, "reason": "scan_result_json must be an array"},
            )
            return

        inserted = 0
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            label_text = str(entry.get("label_text", "")).strip()
            candidate_field_id = str(entry.get("candidate_field_id", "")).strip()
            input_kind = str(entry.get("input_kind", "text")).strip() or "text"
            section_name = str(entry.get("section_name", task.section_id)).strip()
            if not label_text or not candidate_field_id:
                continue
            required_flag = bool(entry.get("required_flag", False))
            draft = FieldDiscoveryDraft(
                draft_id=f"fd_{uuid.uuid4().hex[:12]}",
                run_id=task.run_id,
                section_id=task.section_id,
                portal_url=self._extract_portal_url_from_request(task.request_json),
                label_text=label_text,
                input_kind=input_kind,
                required_flag=required_flag,
                candidate_field_id=candidate_field_id,
                section_name=section_name,
                screenshot_url=screenshot_url,
                status="PENDING",
                notes="",
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            self._session.add(draft)
            inserted += 1

        self._add_event(
            task.run_id,
            "FIELD_DISCOVERY_DRAFTS_CREATED",
            {"task_id": task.task_id, "count": inserted},
        )

    def _extract_portal_url_from_request(self, request_json: str) -> str:
        try:
            parsed = json.loads(request_json)
        except json.JSONDecodeError:
            return ""
        if not isinstance(parsed, dict):
            return ""
        if isinstance(parsed.get("url"), str):
            return parsed["url"]
        request_node = parsed.get("request")
        if isinstance(request_node, dict) and isinstance(request_node.get("url"), str):
            return request_node["url"]
        return ""

    def _extract_browser_session_id_from_request(self, request_json: str) -> str | None:
        try:
            parsed = json.loads(request_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        value = parsed.get("browser_session_id")
        return str(value) if isinstance(value, str) and value else None

    def _extract_scan_id_from_request(self, request_json: str) -> str | None:
        try:
            parsed = json.loads(request_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        value = parsed.get("scan_id")
        return str(value) if isinstance(value, str) and value else None

    def _get_active_catalog_rows(self, section_id: str) -> list[SurveyFieldCatalog]:
        query = (
            select(SurveyFieldCatalog)
            .where(SurveyFieldCatalog.section_id == section_id)
            .where(SurveyFieldCatalog.status == "ACTIVE")
            .order_by(SurveyFieldCatalog.field_id)
        )
        return list(self._session.execute(query).scalars())

    def _catalog_rows_to_fields(self, catalog_rows: list[SurveyFieldCatalog]) -> list[FieldDefinition]:
        return [
            FieldDefinition(
                field_id=row.field_id,
                label_hint=row.label_text,
                input_kind=row.input_kind,
                required=row.required_flag,
            )
            for row in catalog_rows
        ]

    def _pdf_genie_fields_and_values(self, scan_id: str) -> tuple[list[FieldDefinition], dict[str, str]]:
        if not self._session.get(SurveyPdfScan, scan_id):
            raise ValueError(f"Unknown scan_id: {scan_id}")

        rows = list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .where(SurveyPdfDataPointCandidate.status == "GENIE_RESOLVED")
                .where(SurveyPdfDataPointCandidate.genie_value != "")
                .order_by(SurveyPdfDataPointCandidate.nearby_text, SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )
        fields = [
            FieldDefinition(
                field_id=row.candidate_id,
                label_hint=row.label_text,
                input_kind=_catalog_input_kind(row.input_kind),
                required=False,
            )
            for row in rows
        ]
        values = {row.candidate_id: row.genie_value for row in rows}
        return fields, values

    def _count_events(self, run_id: str, event_type: str) -> int:
        query = select(RunEvent).where(RunEvent.run_id == run_id).where(RunEvent.event_type == event_type)
        return len(list(self._session.execute(query).scalars()))
