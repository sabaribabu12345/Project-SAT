from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from databricks.sdk import WorkspaceClient
from apps.api.settings import Settings


def normalize_year(sql: str, genie_year: int) -> str:
    """Replace hardcoded survey year with __SURVEY_YEAR__ placeholder."""
    pattern = re.compile(r"(?<![a-zA-Z0-9_])" + re.escape(str(genie_year)) + r"(?![a-zA-Z0-9_])")
    return pattern.sub("__SURVEY_YEAR__", sql)


def apply_year(template_sql: str, year: int) -> str:
    """Substitute survey-year and CDS registry placeholders for direct execution."""
    rendered = template_sql.replace("__SURVEY_YEAR__", str(year))
    try:
        from apps.api.cds_query_registry import registry_params_for_year

        for placeholder, value in registry_params_for_year(year).items():
            rendered = rendered.replace(placeholder, value)
    except Exception:  # noqa: BLE001
        # Keep legacy Genie SQL usable even if the optional CDS registry is unavailable.
        pass
    return rendered


@dataclass(frozen=True)
class GenieResolution:
    candidate_id: str
    sql_template: str
    table: str
    column: str
    year_column: str
    value: str
    confidence: int
    reason: str


@dataclass(frozen=True)
class GenieMappingChoice:
    master_data_point_id: str | None
    confidence: int | None
    reason: str
    field_key: str | None = None


class GenieMappingClient(Protocol):
    configured: bool

    def choose_master_data_point(
        self,
        *,
        candidate_field_name: str,
        candidate_label_text: str,
        candidate_nearby_text: str,
        options: list[dict[str, Any]],
    ) -> GenieMappingChoice | None:
        ...

    def choose_many_master_data_points(
        self,
        *,
        candidates: list[dict[str, Any]],
    ) -> dict[str, GenieMappingChoice]:
        ...


class DatabricksGenieClient:
    _TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_client: WorkspaceClient | None = None
        self._last_batch_trace: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.databricks_host
            and self._settings.databricks_genie_space_id
            and (
                self._effective_token()
                or (self._settings.databricks_client_id and self._settings.databricks_client_secret)
            )
        )

    def choose_master_data_point(
        self,
        *,
        candidate_field_name: str,
        candidate_label_text: str,
        candidate_nearby_text: str,
        options: list[dict[str, Any]],
    ) -> GenieMappingChoice | None:
        mapped = self.choose_many_master_data_points(
            candidates=[
                {
                    "candidate_id": "__single__",
                    "field_name": candidate_field_name,
                    "label_text": candidate_label_text,
                    "nearby_text": candidate_nearby_text,
                    "options": options,
                }
            ]
        )
        return mapped.get("__single__")

    def choose_many_master_data_points(
        self,
        *,
        candidates: list[dict[str, Any]],
    ) -> dict[str, GenieMappingChoice]:
        if not self.configured:
            raise RuntimeError(
                "Genie provider requires DATABRICKS_HOST, DATABRICKS_GENIE_SPACE_ID, and auth via DATABRICKS_TOKEN/OPENAI_API_KEY or DATABRICKS_CLIENT_ID+DATABRICKS_CLIENT_SECRET"
            )
        if not candidates:
            return {}
        prompt = self._build_batch_prompt(candidates=candidates)
        space_id = self._settings.databricks_genie_space_id.strip()
        trace: dict[str, Any] = {
            "provider": "genie_api",
            "space_id": space_id,
            "candidate_count": len(candidates),
            "prompt_chars": len(prompt),
            "request_candidates": candidates,
        }
        self._last_batch_trace = trace
        started = self._post_json(f"/api/2.0/genie/spaces/{space_id}/start-conversation", {"content": prompt})
        trace["start_conversation_response"] = started
        conversation = started.get("conversation", {})
        message = started.get("message", {})
        conversation_id = str(
            conversation.get("conversation_id") or conversation.get("id") or ""
        ).strip()
        message_id = str(message.get("id") or message.get("message_id") or "").strip()
        trace["conversation_id"] = conversation_id
        trace["message_id"] = message_id
        if not conversation_id or not message_id:
            trace["status"] = "failed"
            trace["error"] = "Genie API did not return conversation_id/message_id"
            raise RuntimeError("Genie API did not return conversation_id/message_id")

        poll_interval = max(1, self._settings.databricks_genie_poll_interval_seconds)
        deadline = time.time() + max(5, self._settings.databricks_genie_poll_timeout_seconds)
        latest: dict[str, Any] = {}
        while time.time() < deadline:
            latest = self._get_json(
                f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}"
            )
            choices = self._extract_batch_choices(latest)
            status = str(latest.get("status") or "").upper()
            trace["last_message_response"] = latest
            trace["terminal_status"] = status
            if choices and status in self._TERMINAL_STATUSES:
                trace["status"] = "completed"
                trace["parsed_choice_count"] = len(choices)
                return choices
            if status in self._TERMINAL_STATUSES:
                if status != "COMPLETED":
                    trace["status"] = "failed"
                    trace["error"] = f"Genie message finished with status={status}"
                    raise RuntimeError(f"Genie message finished with status={status}")
                trace["status"] = "failed"
                trace["error"] = "Genie response did not contain parseable mappings"
                raise RuntimeError("Genie response did not contain parseable mappings")
            time.sleep(poll_interval)
        choices = self._extract_batch_choices(latest)
        trace["last_message_response"] = latest
        trace["terminal_status"] = str(latest.get("status") or "").upper()
        if choices:
            trace["status"] = "completed"
            trace["parsed_choice_count"] = len(choices)
            return choices
        trace["status"] = "failed"
        trace["error"] = "Genie response timed out without parseable mappings"
        raise RuntimeError("Genie response timed out without parseable mappings")

    def resolve_many_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        survey_year: int,
    ) -> dict[str, GenieResolution]:
        """Send up to 50 same-section candidates to Genie; return field-name-keyed resolutions.

        Each candidate dict must have: candidate_id, field_name, section, label_text,
        datapoint_intent, context (nearby_text).
        """
        if not self.configured:
            raise RuntimeError(
                "Genie provider requires DATABRICKS_HOST, DATABRICKS_GENIE_SPACE_ID, and auth"
            )
        if not candidates:
            return {}

        prompt = self._build_resolution_prompt(candidates=candidates, survey_year=survey_year)
        space_id = self._settings.databricks_genie_space_id.strip()

        started = self._post_json(f"/api/2.0/genie/spaces/{space_id}/start-conversation", {"content": prompt})
        conversation = started.get("conversation", {})
        message = started.get("message", {})
        conversation_id = str(conversation.get("conversation_id") or conversation.get("id") or "").strip()
        message_id = str(message.get("id") or message.get("message_id") or "").strip()
        if not conversation_id or not message_id:
            raise RuntimeError("Genie API did not return conversation_id/message_id")

        poll_interval = max(1, self._settings.databricks_genie_poll_interval_seconds)
        deadline = time.time() + max(5, self._settings.databricks_genie_poll_timeout_seconds)
        latest: dict[str, Any] = {}
        statement_id: str = ""

        while time.time() < deadline:
            latest = self._get_json(
                f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}"
            )
            status = str(latest.get("status") or "").upper()
            if status in self._TERMINAL_STATUSES:
                if status != "COMPLETED":
                    raise RuntimeError(f"Genie resolution finished with status={status}")
                statement_id = self._extract_statement_id(latest)
                break
            time.sleep(poll_interval)

        if not statement_id:
            statement_id = self._extract_statement_id(latest)
        if not statement_id:
            raise RuntimeError("Genie resolution did not produce a SQL statement_id")

        raw_sql = self._extract_sql_from_message(latest)
        columns, rows = self._fetch_query_result(space_id, conversation_id, message_id)

        value_by_field: dict[str, str] = {}
        col_upper = [c.upper().strip() for c in columns]

        # Wide format: columns are the field names themselves (one per surveyed field)
        # e.g. columns=["EN_FRSH_FT_MEN_N","EN_FRSH_FT_WMN_N",...], rows=[[2490,3952,...]]
        is_wide = bool(columns) and not (
            len(columns) == 2
            and col_upper[0] in ("FIELD_NAME", "FIELDNAME", "NAME")
            and col_upper[1] in ("VALUE", "VAL")
        )

        if is_wide and rows:
            first_row = rows[0]
            for col, val in zip(col_upper, first_row):
                if col and val is not None:
                    value_by_field[col] = str(val).strip()
        elif rows:
            # Narrow UNION ALL format: each row is [field_name_string, value_string]
            for row in rows:
                if len(row) >= 2 and row[0] is not None:
                    fn = str(row[0]).upper().strip()
                    val = str(row[1]).strip() if row[1] is not None else ""
                    if fn:
                        value_by_field[fn] = val

        cand_by_field = {c["field_name"].upper(): c for c in candidates}

        resolutions: dict[str, GenieResolution] = {}
        sql_template = normalize_year(raw_sql, survey_year) if raw_sql else ""
        fmt = "wide" if is_wide else "narrow"

        for field_upper, value in value_by_field.items():
            cand = cand_by_field.get(field_upper)
            if not cand:
                continue
            resolutions[cand["candidate_id"]] = GenieResolution(
                candidate_id=cand["candidate_id"],
                sql_template=sql_template,
                table="",
                column=field_upper,
                year_column="YEARS",
                value=value,
                confidence=85 if value else 0,
                reason=f"resolved via Genie {fmt}-format query",
            )

        return resolutions

    def get_last_batch_trace(self) -> dict[str, Any] | None:
        return self._last_batch_trace

    def _build_resolution_prompt(self, *, candidates: list[dict[str, Any]], survey_year: int) -> str:
        section = candidates[0].get("section", "") if candidates else ""
        lines = [
            "You are helping map survey PDF fields to Databricks-backed values.",
            "Use the Genie space as a business-user query assistant and keep the answer directly executable in SQL.",
            f"Section: {section}",
            f"Survey year: {survey_year}",
            "",
            "Return one SQL query that produces a narrow result set with exactly two columns: field_name and value.",
            "Use one row per requested field.",
            "If a field cannot be resolved, return that field_name with a NULL value.",
            "Keep the field_name values exact and uppercase.",
            "",
            "I need these values with SQL query for each field:",
            "",
        ]
        for c in candidates:
            field_name = c.get("field_name", "")
            label = c.get("label_text", "")
            intent = c.get("datapoint_intent", "")
            raw_context = c.get("context", c.get("nearby_text", ""))
            # Strip the "| Original nearby text: ..." tail — it's scanner noise
            context = re.sub(r"\s*\|\s*Original nearby text:.*$", "", raw_context, flags=re.IGNORECASE).strip()
            intent_part = f" — {intent}" if intent else ""
            context_part = f'. Context: "{context}"' if context else ""
            lines.append(f"field_name='{field_name}': {label}{intent_part}{context_part}")
        return "\n".join(lines)

    def _extract_statement_id(self, message_payload: dict[str, Any]) -> str:
        attachments = message_payload.get("attachments") or []
        for att in attachments:
            if isinstance(att, dict):
                q = att.get("query") or {}
                sid = str(q.get("statement_id") or "").strip()
                if sid:
                    return sid
        return ""

    def _extract_sql_from_message(self, message_payload: dict[str, Any]) -> str:
        attachments = message_payload.get("attachments") or []
        for att in attachments:
            if isinstance(att, dict):
                q = att.get("query") or {}
                sql = str(q.get("query") or "").strip()
                if sql:
                    return sql
        return ""

    def _fetch_query_result(
        self, space_id: str, conversation_id: str, message_id: str
    ) -> tuple[list[str], list[list[Any]]]:
        """Return (column_names, rows) from the Genie query-result endpoint."""
        path = (
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}"
            f"/messages/{message_id}/query-result"
        )
        result = self._get_json(path)
        try:
            stmt = result.get("statement_response", {})
            schema_cols = stmt.get("manifest", {}).get("schema", {}).get("columns", [])
            columns: list[str] = [c.get("name", "") for c in schema_cols if isinstance(c, dict)]
            data_typed = stmt.get("result", {}).get("data_typed_array", [])
        except Exception:  # noqa: BLE001
            return [], []
        rows: list[list[Any]] = []
        for row in data_typed or []:
            if isinstance(row, list):
                parsed = [cell.get("str") if isinstance(cell, dict) else None for cell in row]
                rows.append(parsed)
            elif isinstance(row, dict) and isinstance(row.get("values"), list):
                parsed = [
                    cell.get("str") if isinstance(cell, dict) and "str" in cell else None
                    for cell in row["values"]
                ]
                rows.append(parsed)
        return columns, rows

    def _build_prompt(
        self,
        *,
        field_name: str,
        label_text: str,
        nearby_text: str,
        options: list[dict[str, Any]],
    ) -> str:
        option_text = json.dumps(options, ensure_ascii=True)
        return (
            "Task: choose the best master datapoint id for one survey PDF field.\n"
            "Return JSON only, no markdown.\n"
            "JSON schema:\n"
            '{"master_data_point_id":"<id-or-empty>","confidence":<0-100 integer>,"reason":"<short>","field_key":"<optional fake_form key or empty>"}\n'
            f"Candidate field_name: {field_name}\n"
            f"Candidate label_text: {label_text}\n"
            f"Candidate nearby_text: {nearby_text}\n"
            f"Allowed options: {option_text}\n"
            "Rules:\n"
            "1) master_data_point_id must be one of allowed options or empty.\n"
            "2) confidence must be integer 0-100.\n"
            "3) Keep reason <= 160 chars.\n"
            "4) field_key may be empty.\n"
        )

    def _build_batch_prompt(self, *, candidates: list[dict[str, Any]]) -> str:
        return (
            "Task: choose the best master datapoint id for each survey PDF field candidate.\n"
            "Prefer candidate label_text and nearby_text; these may already be OpenAI-enriched from the PDF.\n"
            "Return JSON only, no markdown.\n"
            "JSON schema:\n"
            '{"mappings":[{"candidate_id":"<id>","master_data_point_id":"<id-or-empty>","confidence":<0-100 integer>,"reason":"<short>","field_key":"<optional fake_form key or empty>"}]}\n'
            "Rules:\n"
            "1) candidate_id must come from input.\n"
            "2) master_data_point_id must be one of each candidate's allowed options or empty.\n"
            "3) confidence must be integer 0-100.\n"
            "4) Keep each reason <= 160 chars.\n"
            "Input candidates JSON:\n"
            f"{json.dumps(candidates, ensure_ascii=True)}\n"
        )

    def _extract_choice(self, payload: dict[str, Any]) -> GenieMappingChoice | None:
        for text in self._iter_text(payload):
            parsed = self._parse_json_text(text)
            if not isinstance(parsed, dict):
                continue
            data_point_id = str(
                parsed.get("master_data_point_id")
                or parsed.get("data_point_id")
                or parsed.get("id")
                or ""
            ).strip()
            confidence_raw = parsed.get("confidence")
            confidence = None
            if isinstance(confidence_raw, int):
                confidence = max(0, min(100, confidence_raw))
            elif isinstance(confidence_raw, str) and confidence_raw.strip().isdigit():
                confidence = max(0, min(100, int(confidence_raw.strip())))
            reason = str(parsed.get("reason") or parsed.get("explanation") or "").strip()
            field_key = str(parsed.get("field_key") or parsed.get("resolver_field") or "").strip() or None
            return GenieMappingChoice(
                master_data_point_id=data_point_id or None,
                confidence=confidence,
                reason=reason[:160],
                field_key=field_key,
            )
        return None

    def _extract_batch_choices(self, payload: dict[str, Any]) -> dict[str, GenieMappingChoice]:
        by_candidate: dict[str, GenieMappingChoice] = {}
        for text in self._iter_text(payload):
            parsed = self._parse_json_text(text)
            if not isinstance(parsed, dict):
                continue
            mappings = parsed.get("mappings") or parsed.get("results") or parsed.get("choices")
            if not isinstance(mappings, list):
                continue
            for item in mappings:
                if not isinstance(item, dict):
                    continue
                candidate_id = str(item.get("candidate_id") or item.get("id") or "").strip()
                if not candidate_id:
                    continue
                data_point_id = str(
                    item.get("master_data_point_id")
                    or item.get("data_point_id")
                    or item.get("master_id")
                    or ""
                ).strip()
                confidence_raw = item.get("confidence")
                confidence = None
                if isinstance(confidence_raw, int):
                    confidence = max(0, min(100, confidence_raw))
                elif isinstance(confidence_raw, str) and confidence_raw.strip().isdigit():
                    confidence = max(0, min(100, int(confidence_raw.strip())))
                reason = str(item.get("reason") or item.get("explanation") or "").strip()
                field_key = str(item.get("field_key") or item.get("resolver_field") or "").strip() or None
                by_candidate[candidate_id] = GenieMappingChoice(
                    master_data_point_id=data_point_id or None,
                    confidence=confidence,
                    reason=reason[:160],
                    field_key=field_key,
                )
            if by_candidate:
                return by_candidate
        return by_candidate

    def _iter_text(self, payload: Any) -> list[str]:
        texts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, str):
                value = node.strip()
                if value:
                    texts.append(value)
                return
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
                return
            if isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        return texts

    def _parse_json_text(self, value: str) -> dict[str, Any] | None:
        direct = self._try_json(value)
        if isinstance(direct, dict):
            return direct
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            nested = self._try_json(fenced.group(1))
            if isinstance(nested, dict):
                return nested
        first_obj = re.search(r"(\{(?:.|\n)*\})", value)
        if first_obj:
            nested = self._try_json(first_obj.group(1))
            if isinstance(nested, dict):
                return nested
        return None

    def _try_json(self, value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _effective_token(self) -> str:
        # Reuse existing Databricks access token already used for OpenAI-compatible routing when present.
        return (self._settings.databricks_token or self._settings.openai_api_key or "").strip()

    def _workspace_client_for_genie(self) -> WorkspaceClient:
        if self._workspace_client is not None:
            return self._workspace_client
        host = (self._settings.databricks_host or "").strip()
        if not host:
            raise RuntimeError("DATABRICKS_HOST is required for Genie API")
        token = self._effective_token()
        auth_type = (self._settings.databricks_auth_type or "").strip() or None
        if token:
            self._workspace_client = WorkspaceClient(
                host=host,
                token=token,
                auth_type=auth_type or "pat",
                product="survey-automation-v3",
            )
            self._configure_genie_http_timeouts(self._workspace_client)
            return self._workspace_client
        client_id = (self._settings.databricks_client_id or "").strip()
        client_secret = (self._settings.databricks_client_secret or "").strip()
        if client_id and client_secret:
            self._workspace_client = WorkspaceClient(
                host=host,
                client_id=client_id,
                client_secret=client_secret,
                auth_type=auth_type or "oauth-m2m",
                product="survey-automation-v3",
            )
            self._configure_genie_http_timeouts(self._workspace_client)
            return self._workspace_client
        raise RuntimeError("No Databricks credentials available for Genie API")

    def _configure_genie_http_timeouts(self, client: WorkspaceClient) -> None:
        timeout_seconds = max(5, self._settings.databricks_genie_request_timeout_seconds)
        api_client = getattr(client, "api_client", None)
        base_client = getattr(api_client, "_api_client", None)
        if base_client is None:
            return
        if hasattr(base_client, "_http_timeout_seconds"):
            base_client._http_timeout_seconds = timeout_seconds
        if hasattr(base_client, "_retry_timeout_seconds"):
            base_client._retry_timeout_seconds = timeout_seconds

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._workspace_client_for_genie()
        try:
            result = client.api_client.do(
                method="POST",
                path=path,
                body=payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Genie request failed: {exc}") from exc
        if isinstance(result, dict):
            return result
        raise RuntimeError("Unexpected Genie response format")

    def _get_json(self, path: str) -> dict[str, Any]:
        client = self._workspace_client_for_genie()
        try:
            result = client.api_client.do(
                method="GET",
                path=path,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Genie request failed: {exc}") from exc
        if isinstance(result, dict):
            return result
        raise RuntimeError("Unexpected Genie response format")
