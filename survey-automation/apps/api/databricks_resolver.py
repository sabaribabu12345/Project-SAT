from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from apps.api.db.models import SurveyFieldCatalog
from apps.api.databricks_metric_resolvers import resolve_metric_group
from apps.api.settings import Settings, get_settings


@dataclass(frozen=True)
class ResolvedSectionPayload:
    values: dict[str, str]
    missing_fields: list[str]


class DatabricksValueReader(Protocol):
    configured: bool

    def query_value(self, row: SurveyFieldCatalog, survey_year: int) -> str | None:
        ...

    def query_rows(self, sql: str, *, row_limit: int = 1000) -> tuple[list[str], list[list[Any]]]:
        ...


class DatabricksSqlValueReader:
    _IDENTIFIER_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _TERMINAL_STATES = {
        StatementState.CANCELED,
        StatementState.CLOSED,
        StatementState.FAILED,
        StatementState.SUCCEEDED,
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: WorkspaceClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.databricks_sql_warehouse_id)

    def query_value(self, row: SurveyFieldCatalog, survey_year: int) -> str | None:
        if not self._settings.databricks_sql_warehouse_id:
            raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is required for Databricks SQL resolution")

        statement, parameters = self._build_statement(row=row, survey_year=survey_year)
        response = self._client_for_settings().statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._settings.databricks_sql_warehouse_id,
            parameters=parameters,
            row_limit=1,
            wait_timeout=self._settings.databricks_sql_wait_timeout,
        )
        response = self._wait_for_terminal_response(response)
        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            error = response.status.error.message if response.status and response.status.error else "unknown error"
            raise RuntimeError(f"Databricks statement failed: state={state} error={error}")

        rows = response.result.data_array if response.result and response.result.data_array else []
        if not rows or not rows[0]:
            return None
        value = rows[0][0]
        if value is None:
            return None
        return str(value)

    def query_rows(self, sql: str, *, row_limit: int = 1000) -> tuple[list[str], list[list[Any]]]:
        if not self._settings.databricks_sql_warehouse_id:
            raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is required for Databricks SQL resolution")

        response = self._client_for_settings().statement_execution.execute_statement(
            statement=sql,
            warehouse_id=self._settings.databricks_sql_warehouse_id,
            row_limit=row_limit,
            wait_timeout=self._settings.databricks_sql_wait_timeout,
        )
        response = self._wait_for_terminal_response(response)
        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            error = response.status.error.message if response.status and response.status.error else "unknown error"
            raise RuntimeError(f"Databricks statement failed: state={state} error={error}")

        columns = [
            column.name
            for column in (response.manifest.schema.columns if response.manifest and response.manifest.schema else [])
        ]
        rows = response.result.data_array if response.result and response.result.data_array else []
        return columns, rows

    def _client_for_settings(self) -> WorkspaceClient:
        if self._client is None:
            self._client = WorkspaceClient(
                host=self._settings.databricks_host,
                auth_type=self._settings.databricks_auth_type,
                client_id=self._settings.databricks_client_id,
                client_secret=self._settings.databricks_client_secret,
                token=self._settings.databricks_token,
                product="survey-automation-v3",
                product_version="0.1.0",
            )
        return self._client

    def _wait_for_terminal_response(self, response):
        deadline = time.monotonic() + self._settings.databricks_sql_poll_timeout_seconds
        current = response
        while current.status and current.status.state not in self._TERMINAL_STATES:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Databricks statement timed out: statement_id={current.statement_id}")
            time.sleep(2)
            if not current.statement_id:
                raise RuntimeError("Databricks statement response missing statement_id")
            current = self._client_for_settings().statement_execution.get_statement(current.statement_id)
        return current

    def _build_statement(
        self,
        row: SurveyFieldCatalog,
        survey_year: int,
    ) -> tuple[str, list[StatementParameterListItem]]:
        view_name = self._quote_fqn(row.databricks_view, "databricks_view")
        value_column = self._quote_identifier(row.databricks_value_column, "databricks_value_column")

        parameters: list[StatementParameterListItem] = []
        where_clause = ""
        year_column = row.databricks_year_column.strip()
        if year_column:
            where_clause = f" WHERE {self._quote_identifier(year_column, 'databricks_year_column')} = :survey_year"
            parameters.append(StatementParameterListItem(name="survey_year", type="INT", value=str(survey_year)))

        return f"SELECT {value_column} AS value FROM {view_name}{where_clause} LIMIT 1", parameters

    def _quote_fqn(self, value: str, column_name: str) -> str:
        parts = [part.strip() for part in value.split(".") if part.strip()]
        if not 1 <= len(parts) <= 4:
            raise ValueError(f"{column_name} must be a dot-qualified Databricks identifier")
        return ".".join(self._quote_identifier(part, column_name) for part in parts)

    def _quote_identifier(self, value: str, column_name: str) -> str:
        stripped = value.strip()
        if not self._IDENTIFIER_PART_RE.match(stripped):
            raise ValueError(f"{column_name} contains an unsafe identifier: {value}")
        return f"`{stripped}`"


class DatabricksFieldResolver:
    """Resolve field values from literal bindings, Databricks SQL, or local fake-form values."""

    _FAKE_DATABRICKS_VALUES: dict[str, str] = {
        "institution.name": "California State University, Long Beach",
        "institution.city": "Long Beach",
        "institution.state": "CA",
        "institution.zip_code": "90840",
        "institution.website": "https://www.csulb.edu",
        "institution.phone": "562-985-4111",
        "institution.control": "Public",
        "institution.campus_setting": "Urban",
        "institution.calendar": "Semester",
        "institution.first_term": "Fall",
        "institution.accreditation": "WSCUC",
        "institution.ipeds_id": "110583",
    }

    def __init__(
        self,
        settings: Settings | None = None,
        sql_reader: DatabricksValueReader | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._sql_reader = sql_reader or DatabricksSqlValueReader(self._settings)
        self._metric_group_cache: dict[tuple[str, int], dict[str, str]] = {}

    def resolve_section_payload(
        self,
        section_id: str,
        survey_year: int,
        catalog_rows: list[SurveyFieldCatalog],
    ) -> ResolvedSectionPayload:
        del section_id
        resolved: dict[str, str] = {}
        missing_fields: list[str] = []

        for row in catalog_rows:
            try:
                value = self._resolve_value(row=row, survey_year=survey_year)
            except (RuntimeError, TimeoutError, ValueError):
                missing_fields.append(row.field_id)
                continue
            if value is None:
                missing_fields.append(row.field_id)
                continue
            resolved[row.field_id] = value

        return ResolvedSectionPayload(values=resolved, missing_fields=missing_fields)

    def _resolve_value(self, row: SurveyFieldCatalog, survey_year: int) -> str | None:
        # Literal bindings are useful in tests/bootstrap: literal:SomeValue
        if row.databricks_value_column.startswith("literal:"):
            return row.databricks_value_column.split("literal:", maxsplit=1)[1]

        resolver_mode = self._settings.databricks_resolver_mode.strip().lower()
        if resolver_mode not in {"auto", "sql", "fake"}:
            raise ValueError("DATABRICKS_RESOLVER_MODE must be one of: auto, sql, fake")

        metric_value = self._resolve_metric_value(row=row, survey_year=survey_year, resolver_mode=resolver_mode)
        if metric_value is not None:
            return metric_value

        if not row.databricks_view.strip() and not row.databricks_value_column.strip():
            return self._FAKE_DATABRICKS_VALUES.get(row.field_id)
        if row.databricks_view.strip().startswith("survey_"):
            return self._FAKE_DATABRICKS_VALUES.get(row.field_id)

        if resolver_mode == "sql" or (resolver_mode == "auto" and getattr(self._sql_reader, "configured", False)):
            return self._sql_reader.query_value(row=row, survey_year=survey_year)

        return self._FAKE_DATABRICKS_VALUES.get(row.field_id)

    def _resolve_metric_value(self, row: SurveyFieldCatalog, survey_year: int, resolver_mode: str) -> str | None:
        try:
            transform = json.loads(row.transform_json or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(transform, dict):
            return None
        resolver_name = str(transform.get("resolver_name") or "").strip()
        resolver_field = str(transform.get("resolver_field") or "").strip()
        if not resolver_name or not resolver_field:
            return None

        if resolver_mode == "fake" or (resolver_mode == "auto" and not getattr(self._sql_reader, "configured", False)):
            return self._FAKE_DATABRICKS_VALUES.get(row.field_id)

        cache_key = (resolver_name, survey_year)
        if cache_key not in self._metric_group_cache:
            self._metric_group_cache[cache_key] = resolve_metric_group(
                resolver_name=resolver_name,
                survey_year=survey_year,
                sql_reader=self._sql_reader,
            )
        return self._metric_group_cache[cache_key].get(resolver_field)
