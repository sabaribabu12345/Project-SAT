from __future__ import annotations

import pytest

from apps.api.databricks_resolver import DatabricksFieldResolver, DatabricksSqlValueReader
from apps.api.db.models import SurveyFieldCatalog
from apps.api.settings import Settings


class FakeSqlReader:
    configured = True

    def __init__(
        self,
        values: dict[str, str | None],
        row_results: list[list[object]] | None = None,
    ) -> None:
        self.values = values
        self.row_results = row_results or []
        self.queries: list[tuple[str, int]] = []
        self.row_queries: list[str] = []

    def query_value(self, row: SurveyFieldCatalog, survey_year: int) -> str | None:
        self.queries.append((row.field_id, survey_year))
        return self.values.get(row.field_id)

    def query_rows(self, sql: str, *, row_limit: int = 1000) -> tuple[list[str], list[list[object]]]:
        del row_limit
        self.row_queries.append(sql)
        return [], self.row_results


def _catalog_row(
    field_id: str,
    view: str = "production.silver.erss",
    value_column: str = "value",
    year_column: str = "survey_year",
) -> SurveyFieldCatalog:
    return SurveyFieldCatalog(
        field_id=field_id,
        section_id="institution",
        label_text=field_id,
        input_kind="text",
        required_flag=False,
        databricks_view=view,
        databricks_value_column=value_column,
        databricks_year_column=year_column,
        transform_json="{}",
        status="ACTIVE",
    )


def test_resolver_uses_sql_reader_when_auto_mode_is_configured() -> None:
    sql_reader = FakeSqlReader({"institution.name": "CSULB from SQL"})
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="auto", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=sql_reader,
    )

    payload = resolver.resolve_section_payload(
        section_id="institution",
        survey_year=2026,
        catalog_rows=[_catalog_row("institution.name")],
    )

    assert payload.values == {"institution.name": "CSULB from SQL"}
    assert payload.missing_fields == []
    assert sql_reader.queries == [("institution.name", 2026)]


def test_resolver_marks_sql_none_as_missing() -> None:
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=FakeSqlReader({"institution.name": None}),
    )

    payload = resolver.resolve_section_payload(
        section_id="institution",
        survey_year=2026,
        catalog_rows=[_catalog_row("institution.name")],
    )

    assert payload.values == {}
    assert payload.missing_fields == ["institution.name"]


def test_literal_binding_does_not_query_sql() -> None:
    sql_reader = FakeSqlReader({})
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=sql_reader,
    )

    payload = resolver.resolve_section_payload(
        section_id="institution",
        survey_year=2026,
        catalog_rows=[_catalog_row("institution.name", value_column="literal:California State University, Long Beach")],
    )

    assert payload.values == {"institution.name": "California State University, Long Beach"}
    assert sql_reader.queries == []


def test_unbound_field_is_missing_without_querying_sql() -> None:
    sql_reader = FakeSqlReader({})
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=sql_reader,
    )

    payload = resolver.resolve_section_payload(
        section_id="pdf_master",
        survey_year=2026,
        catalog_rows=[_catalog_row("dp.fake_form.unknown", view="", value_column="", year_column="")],
    )

    assert payload.values == {}
    assert payload.missing_fields == ["dp.fake_form.unknown"]
    assert sql_reader.queries == []


def test_placeholder_survey_view_is_missing_without_querying_sql() -> None:
    sql_reader = FakeSqlReader({})
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=sql_reader,
    )

    payload = resolver.resolve_section_payload(
        section_id="pdf_master",
        survey_year=2026,
        catalog_rows=[_catalog_row("dp.catalog.placeholder", view="survey_institution_view")],
    )

    assert payload.values == {}
    assert payload.missing_fields == ["dp.catalog.placeholder"]
    assert sql_reader.queries == []


def test_metric_resolver_uses_group_query_and_cache() -> None:
    sql_reader = FakeSqlReader(
        {},
        row_results=[
            ["applied", "men", 10],
            ["applied", "women", 20],
            ["applied", "other", 1],
            ["admitted", "men", 3],
            ["admitted", "women", 4],
            ["admitted", "other", 0],
        ],
    )
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=sql_reader,
    )
    rows = [
        _catalog_row(
            "dp.fake_form.applied.total",
            view="",
            value_column="",
            year_column="",
        ),
        _catalog_row(
            "dp.fake_form.admitted.total",
            view="",
            value_column="",
            year_column="",
        ),
    ]
    rows[0].transform_json = (
        '{"resolver_name":"fall_admissions_counts","resolver_field":"applied_total"}'
    )
    rows[1].transform_json = (
        '{"resolver_name":"fall_admissions_counts","resolver_field":"admitted_total"}'
    )

    payload = resolver.resolve_section_payload(section_id="pdf_master", survey_year=2026, catalog_rows=rows)

    assert payload.values == {
        "dp.fake_form.applied.total": "31",
        "dp.fake_form.admitted.total": "7",
    }
    assert payload.missing_fields == []
    assert len(sql_reader.row_queries) == 1
    assert "production.silver.ersa" in sql_reader.row_queries[0]


def test_sql_reader_builds_parameterized_statement() -> None:
    reader = DatabricksSqlValueReader(Settings(databricks_sql_warehouse_id="warehouse-id"))

    statement, parameters = reader._build_statement(  # noqa: SLF001
        row=_catalog_row("enrollment.total", view="production.silver.erss", value_column="headcount"),
        survey_year=2026,
    )

    assert statement == (
        "SELECT `headcount` AS value FROM `production`.`silver`.`erss` "
        "WHERE `survey_year` = :survey_year LIMIT 1"
    )
    assert [(param.name, param.type, param.value) for param in parameters] == [("survey_year", "INT", "2026")]


def test_sql_reader_rejects_unsafe_identifiers() -> None:
    reader = DatabricksSqlValueReader(Settings(databricks_sql_warehouse_id="warehouse-id"))

    with pytest.raises(ValueError, match="unsafe identifier"):
        reader._build_statement(  # noqa: SLF001
            row=_catalog_row("bad", view="production.silver.erss;drop", value_column="value"),
            survey_year=2026,
        )


def test_resolver_marks_bad_binding_missing_without_failing_other_fields() -> None:
    resolver = DatabricksFieldResolver(
        settings=Settings(databricks_resolver_mode="sql", databricks_sql_warehouse_id="warehouse-id"),
        sql_reader=FakeSqlReader({"good": "ok"}),
    )

    payload = resolver.resolve_section_payload(
        section_id="pdf_master",
        survey_year=2026,
        catalog_rows=[
            _catalog_row("bad", view="production.silver.erss", value_column="institution.campus_setting"),
            _catalog_row("good"),
        ],
    )

    assert payload.values == {"good": "ok"}
    assert payload.missing_fields == ["bad"]
