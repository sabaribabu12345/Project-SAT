from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from databricks.sdk import WorkspaceClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.databricks_genie_client import apply_year
from apps.api.databricks_resolver import DatabricksSqlValueReader
from apps.api.db.models import (
    AnalystSqlFieldMapping,
    AnalystSqlMappingDraft,
    AnalystSqlQuery,
    SurveyPdfDataPointCandidate,
    SurveyPdfScan,
)
from apps.api.settings import Settings


_CDS_SECTIONS: list[tuple[str, str, str]] = [
    # (section_id, label, field_name_prefix)
    ("CDS", "Respondent Info (CDS_)", "CDS_"),
    ("A", "Section A — Addresses & General Info", "A"),
    ("B", "Section B — Enrollment & Diversity", "B"),
    ("C", "Section C — First-Time Freshmen Admissions", "C"),
    ("D", "Section D — Transfer Admissions", "D"),
    ("E", "Section E — Academic Offerings / Programs", "E"),
    ("F", "Section F — Student Life", "F"),
    ("G", "Section G — Annual Expenses", "G"),
    ("H", "Section H — Financial Aid", "H"),
    ("I", "Section I — Instructional Faculty & Class Size", "I"),
    ("J", "Section J — Degrees Conferred", "J"),
    ("FRSH", "Freshmen Profile (FRSH_)", "FRSH_"),
    ("UG", "Undergrad Totals (UG_)", "UG_"),
    ("SAT", "SAT Scores (SAT)", "SAT"),
    ("ACT", "ACT Scores (ACT_)", "ACT_"),
    ("TUIT", "Tuition & Costs (TUIT_)", "TUIT_"),
    ("SCHOL", "Scholarships (SCHOL_)", "SCHOL_"),
    ("LOAN", "Loans (LOAN_)", "LOAN_"),
    ("LIFE", "Living Costs (LIFE_)", "LIFE_"),
]

_SECTION_PREFIX_MAP: dict[str, str] = {sid: pfx for sid, _, pfx in _CDS_SECTIONS}


def cds_section_for(field_name: str) -> tuple[str, str]:
    """Return (section_id, section_label) for a given field_name."""
    fn_upper = field_name.upper()
    for sid, label, pfx in _CDS_SECTIONS:
        if fn_upper.startswith(pfx.upper()):
            return sid, label
    # Fall back to the first token before '_'
    parts = fn_upper.split("_", 1)
    fallback = parts[0]
    return fallback, f"Other ({fallback})"


def _section_field_prefix(section_id: str) -> str:
    return _SECTION_PREFIX_MAP.get(section_id.upper(), section_id.upper() + "_")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stringify_rows(rows: list[list[Any]]) -> list[list[str]]:
    return [["" if value is None else str(value) for value in row] for row in rows]


def _safe_json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _value_from_result(columns: list[str], rows: list[list[Any]], row_index: int, source_column: str) -> str:
    if row_index < 0 or row_index >= len(rows):
        return ""
    normalized_columns = {column.strip().lower(): idx for idx, column in enumerate(columns)}
    column_index = normalized_columns.get(source_column.strip().lower())
    if column_index is None:
        return ""
    row = rows[row_index]
    if column_index >= len(row):
        return ""
    value = row[column_index]
    return "" if value is None else str(value).strip()


class SqlMappingProposalClient(Protocol):
    configured: bool

    def propose_mappings(
        self,
        *,
        query: AnalystSqlQuery,
        candidates: list[dict[str, Any]],
        columns: list[str],
        rows: list[list[str]],
        max_drafts: int = 50,
    ) -> list[dict[str, Any]]:
        ...


class DatabricksServingSqlMapper:
    """Use Databricks model serving to propose SQL-result-to-PDF-field mappings.

    The model proposes only drafts. Analyst approval is required before values are
    written back to candidates or reused on reruns.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_client: WorkspaceClient | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.databricks_host
            and self._settings.databricks_sql_mapping_model
            and (
                self._effective_token()
                or (self._settings.databricks_client_id and self._settings.databricks_client_secret)
            )
        )

    def propose_mappings(
        self,
        *,
        query: AnalystSqlQuery,
        candidates: list[dict[str, Any]],
        columns: list[str],
        rows: list[list[str]],
        max_drafts: int = 50,
    ) -> list[dict[str, Any]]:
        if not candidates or not columns or not rows:
            return []
        if not self.configured:
            return _heuristic_proposals(candidates=candidates, columns=columns, rows=rows, max_drafts=max_drafts)
        payload = self._build_payload(
            query=query,
            candidates=candidates,
            columns=columns,
            rows=rows,
            max_drafts=max_drafts,
        )
        raw = self._post_serving_json(payload)
        parsed = self._parse_response(raw)
        return parsed[:max_drafts] if parsed else []

    def _build_payload(
        self,
        *,
        query: AnalystSqlQuery,
        candidates: list[dict[str, Any]],
        columns: list[str],
        rows: list[list[str]],
        max_drafts: int,
    ) -> dict[str, Any]:
        compact_candidates = [
            {
                "candidate_id": item["candidate_id"],
                "field_name": item["field_name"],
                "label_text": item["label_text"],
                "datapoint_intent": item.get("datapoint_intent", ""),
            }
            for item in candidates[:200]
        ]
        compact_rows = rows[:50]
        return {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You map analyst SQL result rows to Common Data Set PDF fields. "
                        "Use the SQL, column names, sample rows, field names, labels, and datapoint intent. "
                        "Return JSON only. Do not invent values. Every mapping requires a source_row_index and source_column."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return schema: "
                        '{"mappings":[{"candidate_id":"<id>","field_name":"<pdf field>",'
                        '"source_row_index":0,"source_column":"<column>","confidence":90,'
                        '"reason":"<short analyst-readable reason>"}]}'
                        f"\nMax mappings: {max_drafts}"
                        f"\nQuery name: {query.name}"
                        f"\nSurvey year: {query.survey_year}"
                        f"\nSQL:\n{query.sql_text}"
                        f"\nColumns:\n{json.dumps(columns, ensure_ascii=True)}"
                        f"\nRows:\n{json.dumps(compact_rows, ensure_ascii=True)}"
                        f"\nPDF candidates:\n{json.dumps(compact_candidates, ensure_ascii=True)}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": min(12000, max(2000, max_drafts * 220)),
        }

    def _post_serving_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._settings.databricks_sql_mapping_model.strip()
        timeout_seconds = max(5, self._settings.databricks_sql_mapping_timeout_seconds)
        client = self._workspace_client_for_serving()
        do_kwargs: dict[str, Any] = {"method": "POST", "path": f"/serving-endpoints/{model}/invocations", "body": payload}
        result: Any = None
        for key in ("timeout", "timeout_seconds", "request_timeout"):
            do_kwargs[key] = timeout_seconds
            try:
                result = client.api_client.do(**do_kwargs)
                break
            except TypeError:
                do_kwargs.pop(key, None)
        if result is None:
            result = client.api_client.do(method="POST", path=f"/serving-endpoints/{model}/invocations", body=payload)
        if isinstance(result, dict):
            return result
        raise RuntimeError("Unexpected Databricks SQL mapping response format")

    def _parse_response(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        text = _extract_message_text(raw)
        parsed = _parse_json_text(text)
        mappings = parsed.get("mappings") if isinstance(parsed, dict) else None
        if not isinstance(mappings, list):
            return []
        proposals: list[dict[str, Any]] = []
        for row in mappings:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or "").strip()
            source_column = str(row.get("source_column") or "").strip()
            if not candidate_id or not source_column:
                continue
            try:
                source_row_index = int(row.get("source_row_index", 0))
            except (TypeError, ValueError):
                source_row_index = 0
            try:
                confidence = int(float(row.get("confidence", 0)))
            except (TypeError, ValueError):
                confidence = 0
            proposals.append(
                {
                    "candidate_id": candidate_id,
                    "field_name": str(row.get("field_name") or "").strip(),
                    "source_row_index": max(0, source_row_index),
                    "source_column": source_column,
                    "confidence": max(0, min(100, confidence)),
                    "reason": str(row.get("reason") or "Proposed by Databricks Serving model").strip(),
                }
            )
        return proposals

    def _effective_token(self) -> str:
        return (self._settings.databricks_token or self._settings.openai_api_key or "").strip()

    def _workspace_client_for_serving(self) -> WorkspaceClient:
        if self._workspace_client is not None:
            return self._workspace_client
        host = (self._settings.databricks_host or "").strip()
        if not host:
            raise RuntimeError("DATABRICKS_HOST is required for analyst SQL mapping")
        token = self._effective_token()
        auth_type = (self._settings.databricks_auth_type or "").strip() or None
        if token:
            self._workspace_client = WorkspaceClient(host=host, token=token, auth_type=auth_type or "pat")
            return self._workspace_client
        client_id = (self._settings.databricks_client_id or "").strip()
        client_secret = (self._settings.databricks_client_secret or "").strip()
        if client_id and client_secret:
            self._workspace_client = WorkspaceClient(
                host=host,
                client_id=client_id,
                client_secret=client_secret,
                auth_type=auth_type or "oauth-m2m",
            )
            return self._workspace_client
        raise RuntimeError("No Databricks credentials available for analyst SQL mapping")


def _heuristic_proposals(
    *,
    candidates: list[dict[str, Any]],
    columns: list[str],
    rows: list[list[str]],
    max_drafts: int,
) -> list[dict[str, Any]]:
    value_columns = [column for column in columns if column.strip().lower() not in {"metric", "label", "field", "pdf_field"}]
    metric_idx = next((i for i, col in enumerate(columns) if col.strip().lower() in {"metric", "label", "field", "pdf_field"}), 0)
    proposals: list[dict[str, Any]] = []
    for candidate in candidates:
        hay = f"{candidate.get('field_name','')} {candidate.get('label_text','')} {candidate.get('datapoint_intent','')}".lower()
        best: tuple[int, int, str] | None = None
        for row_index, row in enumerate(rows):
            row_label = str(row[metric_idx] if len(row) > metric_idx else "").lower()
            score = 0
            if "applied" in row_label and ("app" in hay or "application" in hay):
                score += 70
            if "admitted" in row_label and ("admit" in hay or "admitted" in hay):
                score += 70
            if "enrolled" in row_label and "enroll" in hay:
                score += 70
            source_column = "total" if "total" in {col.lower() for col in value_columns} else (value_columns[-1] if value_columns else "")
            if source_column:
                score += 10
            if best is None or score > best[0]:
                best = (score, row_index, source_column)
        if best and best[0] >= 50:
            proposals.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "field_name": candidate["field_name"],
                    "source_row_index": best[1],
                    "source_column": best[2],
                    "confidence": min(89, best[0]),
                    "reason": "Local fallback matched the PDF label to the SQL row label.",
                }
            )
        if len(proposals) >= max_drafts:
            break
    return proposals


class AnalystSqlMappingService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        sql_reader: Any | None = None,
        mapper: SqlMappingProposalClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._sql_reader = sql_reader or DatabricksSqlValueReader(settings)
        self._mapper = mapper or DatabricksServingSqlMapper(settings)

    def preview_sql(
        self,
        *,
        scan_id: str,
        name: str,
        sql_text: str,
        survey_year: int,
        row_limit: int,
    ) -> AnalystSqlQuery:
        scan = self._session.get(SurveyPdfScan, scan_id)
        if not scan:
            raise ValueError(f"Unknown scan_id: {scan_id}")
        cleaned_sql = sql_text.strip()
        if not cleaned_sql:
            raise ValueError("SQL text is required")
        columns, rows = self._sql_reader.query_rows(apply_year(cleaned_sql, survey_year), row_limit=row_limit)
        query = AnalystSqlQuery(
            query_id=f"asql_{uuid.uuid4().hex[:12]}",
            scan_id=scan_id,
            name=name.strip() or "Analyst SQL",
            sql_text=cleaned_sql,
            survey_year=survey_year,
            columns_json=json.dumps([str(column) for column in columns]),
            sample_rows_json=json.dumps(_stringify_rows(rows)),
            row_count=len(rows),
            status="PREVIEWED",
        )
        self._session.add(query)
        self._session.commit()
        return query

    def auto_map(self, *, query_id: str, max_drafts: int, section_filter: str = "") -> list[AnalystSqlMappingDraft]:
        query = self._session.get(AnalystSqlQuery, query_id)
        if not query:
            raise ValueError(f"Unknown query_id: {query_id}")
        columns = [str(value) for value in _safe_json_list(query.columns_json)]
        rows = _stringify_rows(_safe_json_list(query.sample_rows_json))
        candidates = self._candidate_payload(query.scan_id)
        if section_filter:
            prefix = _section_field_prefix(section_filter.strip())
            candidates = [c for c in candidates if c["field_name"].upper().startswith(prefix)]
        proposals = self._mapper.propose_mappings(
            query=query,
            candidates=candidates,
            columns=columns,
            rows=rows,
            max_drafts=max_drafts,
        )
        candidate_by_id = {item["candidate_id"]: item for item in candidates}
        drafts: list[AnalystSqlMappingDraft] = []
        for proposal in proposals:
            candidate = candidate_by_id.get(str(proposal.get("candidate_id") or ""))
            if not candidate:
                continue
            source_column = str(proposal.get("source_column") or "").strip()
            try:
                row_index = int(proposal.get("source_row_index", 0))
            except (TypeError, ValueError):
                row_index = 0
            value_preview = _value_from_result(columns, rows, row_index, source_column)
            if not value_preview:
                continue
            draft = AnalystSqlMappingDraft(
                draft_id=f"asqldraft_{uuid.uuid4().hex[:12]}",
                query_id=query.query_id,
                scan_id=query.scan_id,
                candidate_id=str(candidate["candidate_id"]),
                field_name=str(candidate["field_name"]),
                source_row_index=max(0, row_index),
                source_column=source_column,
                value_preview=value_preview,
                confidence=max(0, min(100, int(proposal.get("confidence") or 0))),
                reason=str(proposal.get("reason") or "Proposed by Databricks Serving model")[:1000],
                status="PENDING_APPROVAL",
            )
            self._session.add(draft)
            drafts.append(draft)
        self._session.commit()
        return drafts

    def approve_draft(
        self,
        *,
        draft_id: str,
        source_row_index: int | None = None,
        source_column: str | None = None,
    ) -> tuple[AnalystSqlMappingDraft, AnalystSqlFieldMapping, str]:
        draft = self._session.get(AnalystSqlMappingDraft, draft_id)
        if not draft:
            raise ValueError(f"Unknown draft_id: {draft_id}")
        query = self._session.get(AnalystSqlQuery, draft.query_id)
        if not query:
            raise ValueError(f"Unknown query_id for draft: {draft.query_id}")
        candidate = self._session.get(SurveyPdfDataPointCandidate, draft.candidate_id)
        if not candidate:
            raise ValueError(f"Unknown candidate_id for draft: {draft.candidate_id}")
        row_index = draft.source_row_index if source_row_index is None else source_row_index
        column = draft.source_column if source_column is None else source_column.strip()
        columns = [str(value) for value in _safe_json_list(query.columns_json)]
        rows = _stringify_rows(_safe_json_list(query.sample_rows_json))
        value = _value_from_result(columns, rows, row_index, column)
        if not value:
            raise ValueError("Approved mapping did not produce a value")
        mapping = self._approved_mapping_for(query_id=query.query_id, candidate_id=draft.candidate_id)
        if mapping is None:
            mapping = AnalystSqlFieldMapping(
                mapping_id=f"asqlmap_{uuid.uuid4().hex[:12]}",
                query_id=query.query_id,
                scan_id=query.scan_id,
                candidate_id=draft.candidate_id,
                field_name=draft.field_name,
            )
            self._session.add(mapping)
        mapping.source_row_index = row_index
        mapping.source_column = column
        mapping.confidence = draft.confidence
        mapping.created_from_draft_id = draft.draft_id
        mapping.status = "APPROVED"
        mapping.updated_at = _utc_now()
        draft.source_row_index = row_index
        draft.source_column = column
        draft.value_preview = value
        draft.status = "APPROVED"
        draft.updated_at = _utc_now()
        self._apply_value(candidate=candidate, query=query, value=value, confidence=draft.confidence, reason=draft.reason)
        self._session.commit()
        return draft, mapping, value

    def rerun_approved(self, *, query_id: str, survey_year: int) -> dict[str, int]:
        query = self._session.get(AnalystSqlQuery, query_id)
        if not query:
            raise ValueError(f"Unknown query_id: {query_id}")
        mappings = list(
            self._session.execute(
                select(AnalystSqlFieldMapping)
                .where(AnalystSqlFieldMapping.query_id == query_id)
                .where(AnalystSqlFieldMapping.status == "APPROVED")
            ).scalars()
        )
        refreshed = missing = sql_errors = 0
        try:
            columns, rows = self._sql_reader.query_rows(apply_year(query.sql_text, survey_year), row_limit=max(1000, len(mappings)))
        except Exception:  # noqa: BLE001
            sql_errors = len(mappings) or 1
            return {"refreshed": 0, "missing": 0, "sql_errors": sql_errors}
        query.survey_year = survey_year
        query.columns_json = json.dumps([str(column) for column in columns])
        query.sample_rows_json = json.dumps(_stringify_rows(rows))
        query.row_count = len(rows)
        query.updated_at = _utc_now()
        for mapping in mappings:
            candidate = self._session.get(SurveyPdfDataPointCandidate, mapping.candidate_id)
            if not candidate:
                missing += 1
                continue
            value = _value_from_result([str(c) for c in columns], rows, mapping.source_row_index, mapping.source_column)
            if not value:
                missing += 1
                continue
            self._apply_value(
                candidate=candidate,
                query=query,
                value=value,
                confidence=mapping.confidence,
                reason=f"Rerun approved Analyst SQL mapping {mapping.mapping_id}",
            )
            refreshed += 1
        self._session.commit()
        return {"refreshed": refreshed, "missing": missing, "sql_errors": sql_errors}

    def _candidate_payload(self, scan_id: str) -> list[dict[str, Any]]:
        rows = list(
            self._session.execute(
                select(SurveyPdfDataPointCandidate)
                .where(SurveyPdfDataPointCandidate.scan_id == scan_id)
                .order_by(SurveyPdfDataPointCandidate.field_name)
            ).scalars()
        )
        return [
            {
                "candidate_id": row.candidate_id,
                "field_name": row.field_name,
                "label_text": row.label_text,
                "datapoint_intent": row.datapoint_intent,
                "nearby_text": row.nearby_text,
                "status": row.status,
                "current_value": row.genie_value,
            }
            for row in rows
        ]

    def _approved_mapping_for(self, *, query_id: str, candidate_id: str) -> AnalystSqlFieldMapping | None:
        return self._session.execute(
            select(AnalystSqlFieldMapping)
            .where(AnalystSqlFieldMapping.query_id == query_id)
            .where(AnalystSqlFieldMapping.candidate_id == candidate_id)
            .where(AnalystSqlFieldMapping.status == "APPROVED")
        ).scalar_one_or_none()

    def _apply_value(
        self,
        *,
        candidate: SurveyPdfDataPointCandidate,
        query: AnalystSqlQuery,
        value: str,
        confidence: int,
        reason: str,
    ) -> None:
        candidate.genie_sql_template = query.sql_text
        candidate.genie_table = "analyst_sql"
        candidate.genie_column = candidate.field_name
        candidate.genie_year_column = "analyst_sql_query"
        candidate.genie_value = value
        candidate.genie_confidence = max(0, min(100, confidence))
        candidate.genie_reason = f"Analyst SQL {query.name}: {reason}".strip()
        candidate.genie_resolved_at = _utc_now()
        candidate.status = "GENIE_RESOLVED"
        candidate.updated_at = _utc_now()


def _extract_message_text(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                extracted = _content_to_text(content)
                if extracted:
                    return extracted
    predictions = raw.get("predictions")
    if isinstance(predictions, list):
        for prediction in predictions:
            if isinstance(prediction, dict) and isinstance(prediction.get("text"), str):
                return str(prediction["text"]).strip()
    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]).strip())
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return str(content["text"]).strip()
    return ""


def _parse_json_text(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    for candidate in (
        value,
        re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE | re.DOTALL),
    ):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{(?:.|\n)*\})", value)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
