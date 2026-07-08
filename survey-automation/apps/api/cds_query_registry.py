from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CdsRegistryQuery:
    query_id: str
    section: str
    field_names: frozenset[str]
    sql_template: str
    source_table: str


_FIELD_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_SECTION_RE = re.compile(r"^##\s+(Q-[^\n]+)\n(?P<body>.*?)(?=^##\s+Q-|^#\s+Missing|\Z)", re.MULTILINE | re.DOTALL)
_SQL_RE = re.compile(r"\*\*SQL:\*\*\s*\n\s*```sql\n(?P<sql>.*?)\n```", re.DOTALL)


def normalize_pdf_field_name(value: str) -> str:
    return re.sub(r"\s+", "", value).upper().strip()


def registry_params_for_year(year: int) -> dict[str, str]:
    return {
        "__SURVEY_YEAR__": str(year),
        "__FALL_TERM__": f"{year}4",
        "__FALL_YEAR__": str(year),
        "__FALL_TERM_CODE__": "4",
        "__PRIOR_FALL_TERM__": f"{year - 1}4",
        "__PRIOR_FALL_YEAR__": str(year - 1),
        "__ADMISSION_FALL_TERM__": f"{year}4",
        "__ADMISSION_FALL_YEAR__": str(year),
        "__FACULTY_FALL_TERM__": f"2{year % 100:02d}4",
        "__FACULTY_NRA_TM_TABLE__": f"TYLERN.CDS_FACULTY_NRA_TM_F{year % 100:02d}_TBL",
        "__GRAD_RATE_COHORT_CURRENT__": f"{year - 6}4",
        "__GRAD_RATE_COHORT_CURRENT_YEAR__": str(year - 6),
        "__GRAD_RATE_COHORT_PRIOR__": f"{year - 7}4",
        "__GRAD_RATE_COHORT_PRIOR_YEAR__": str(year - 7),
        "__FULL_TIME_CREDIT_THRESHOLD__": "120",
        "__DEGREE_AWARD_TERMS_SQL_LIST__": ", ".join(
            str(term) for term in (int(f"{year - 1}3"), int(f"{year - 1}4"), int(f"{year}1"), int(f"{year}2"))
        ),
    }


def apply_registry_year(sql: str, year: int) -> str:
    rendered = sql
    for placeholder, value in registry_params_for_year(year).items():
        rendered = rendered.replace(placeholder, value)
    return rendered


class CdsQueryRegistry:
    def __init__(self, queries: list[CdsRegistryQuery]) -> None:
        self._queries = queries
        self._field_to_query: dict[str, CdsRegistryQuery] = {}
        for query in queries:
            for field_name in query.field_names:
                self._field_to_query.setdefault(field_name, query)

    @classmethod
    def default(cls) -> "CdsQueryRegistry":
        return _default_registry()

    def query_for_field(self, field_name: str) -> CdsRegistryQuery | None:
        return self._field_to_query.get(normalize_pdf_field_name(field_name))

    def extract_value(
        self,
        *,
        query: CdsRegistryQuery,
        field_name: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> str | None:
        normalized_field = normalize_pdf_field_name(field_name)
        if not rows:
            return None
        if len(columns) == 1 and columns[0].strip().lower() == "value":
            value = rows[0][0] if rows[0] else None
            return _stringify_value(value)

        column_index = {name.strip().lower(): index for index, name in enumerate(columns)}
        if "pdf_field" in column_index and "value" in column_index:
            field_idx = column_index["pdf_field"]
            value_idx = column_index["value"]
            for row in rows:
                if len(row) > max(field_idx, value_idx) and normalize_pdf_field_name(str(row[field_idx])) == normalized_field:
                    return _stringify_value(row[value_idx])

        mapper = _DIMENSIONAL_MAPPERS.get(query.query_id)
        if mapper:
            return mapper(normalized_field, column_index, rows)
        return None


@lru_cache(maxsize=1)
def _default_registry() -> CdsQueryRegistry:
    markdown = _load_registry_markdown()
    queries = _parse_registry_markdown(markdown)
    return CdsQueryRegistry(queries)


def _load_registry_markdown() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "cds_sql_query_registry.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return _FALLBACK_REGISTRY_MARKDOWN


def _parse_registry_markdown(markdown: str) -> list[CdsRegistryQuery]:
    queries: list[CdsRegistryQuery] = []
    for match in _SECTION_RE.finditer(markdown):
        query_id = match.group(1).split(":", maxsplit=1)[0].strip()
        body = match.group("body")
        sql_match = _SQL_RE.search(body)
        if not sql_match:
            continue
        sql = _parameterize_sql(sql_match.group("sql"))
        fields = _extract_field_names(body[: sql_match.start()])
        fields.update(_FIELD_OVERRIDES.get(query_id, set()))
        if not fields:
            continue
        queries.append(
            CdsRegistryQuery(
                query_id=query_id,
                section=_section_from_query_id(query_id),
                field_names=frozenset(fields),
                sql_template=sql,
                source_table=_infer_source_table(sql),
            )
        )
    return queries


def _extract_field_names(text: str) -> set[str]:
    fields: set[str] = set()
    for match in _FIELD_RE.finditer(text):
        token = normalize_pdf_field_name(match.group(0))
        if token.startswith(("IRAMASTER_", "IPEDS_")):
            continue
        if any(token.endswith(suffix) for suffix in ("_N", "_P", "_D", "_URL", "_FT", "_ALL")):
            fields.add(token)
    return fields


def _parameterize_sql(sql: str) -> str:
    replacements = {
        "${fall_term}": "__FALL_TERM__",
        "${prior_fall_term}": "__PRIOR_FALL_TERM__",
        "${admission_fall_term}": "__ADMISSION_FALL_TERM__",
        "${admission_fall_year}": "__ADMISSION_FALL_YEAR__",
        "${faculty_fall_term}": "__FACULTY_FALL_TERM__",
        "${faculty_nra_tm_table}": "__FACULTY_NRA_TM_TABLE__",
        "${degree_award_terms_sql_list}": "__DEGREE_AWARD_TERMS_SQL_LIST__",
        "${full_time_credit_threshold}": "__FULL_TIME_CREDIT_THRESHOLD__",
        "${grad_rate_cohort_current}": "__GRAD_RATE_COHORT_CURRENT__",
        "${grad_rate_cohort_prior}": "__GRAD_RATE_COHORT_PRIOR__",
        "${cohort_term}": "__GRAD_RATE_COHORT_CURRENT__",
    }
    rendered = sql.strip().rstrip(";")
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    rendered = _rewrite_deprecated_ers_tables(rendered)
    rendered = _rewrite_deprecated_functions(rendered)
    rendered = _rewrite_fin_aid_table(rendered)
    rendered = _rewrite_hegis_tables(rendered)
    rendered = _rewrite_oracle_string_functions(rendered)
    rendered = _rewrite_f1_age_expression(rendered)
    rendered = _rewrite_grad_rate_term_math(rendered)
    return _rewrite_legacy_term_filters(rendered)


def _rewrite_fin_aid_table(sql: str) -> str:
    if "cds_fin_aid_status" not in sql:
        return sql
    fin_aid_cte = (
        "(\n"
        "  SELECT\n"
        "      EMPLID,\n"
        "      MAX(CASE WHEN FIN_AID_TYPE = 'G' AND COALESCE(CAST(ACCEPT_AMOUNT AS DOUBLE), 0) > 0 THEN 'Yes' ELSE 'No' END) AS PELL,\n"
        "      MAX(CASE WHEN FIN_AID_TYPE = 'L' AND COALESCE(CAST(ACCEPT_AMOUNT AS DOUBLE), 0) > 0 THEN 'Yes' ELSE 'No' END) AS STAFFORD\n"
        "  FROM bronze.cms.ps_stdnt_awards\n"
        "  GROUP BY EMPLID\n"
        ")"
    )
    return sql.replace("LEFT JOIN cds_fin_aid_status A", f"LEFT JOIN {fin_aid_cte} A")


def _rewrite_hegis_tables(sql: str) -> str:
    return re.sub(
        r"(?<![A-Za-z0-9_])(?:iramaster|IRAMASTER)\.SS_HEGIS_CIP(?![A-Za-z0-9_])",
        "production.reference.ira_ss_hegis_cip",
        sql,
    )


def _rewrite_oracle_string_functions(sql: str) -> str:
    rendered = re.sub(
        r"CONCAT\(TO_CHAR\((\w+)\.YEARS, '9999'\), (\w+)\.TERM\)",
        r"CAST(CAST(\1.YEARS AS INT) * 10 + CAST(\2.TERM AS INT) AS STRING)",
        sql,
    )
    return rendered.replace("T1.SSN = T2.SSN", "T1.EMPLID = T2.EMPLID")


def _rewrite_f1_age_expression(sql: str) -> str:
    return sql.replace(
        "ROUND(((TO_DATE(('15-' || 'SEP-' || A.YEARS), 'DD, MON, YYYY') - TO_DATE(A.BIRTH_DATE))/365), 2) AS age",
        "ROUND(DATEDIFF(TO_DATE(CONCAT('15-SEP-', CAST(A.YEARS AS INT))), CAST(A.BIRTH_DATE AS DATE)) / 365.25, 2) AS age",
    )


def _rewrite_grad_rate_term_math(sql: str) -> str:
    if "grad_4yr_n" not in sql:
        return sql
    replacements = {
        "YEARS || TERM AS degree_term": "CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) AS degree_term",
        "ORDER BY YEARS || TERM ASC": "ORDER BY CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) ASC",
        "T1.YEARS || T1.TERM AS cohort_term": "CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT) AS cohort_term",
        "(T1.YEARS || T1.TERM)+8": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+8",
        "(T1.YEARS || T1.TERM)+39": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+39",
        "(T1.YEARS || T1.TERM)+40": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+40",
        "(T1.YEARS || T1.TERM)+49": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+49",
        "(T1.YEARS || T1.TERM)+50": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+50",
        "(T1.YEARS || T1.TERM)+59": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT))+59",
    }
    rendered = sql
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    return rendered


def _rewrite_legacy_term_filters(sql: str) -> str:
    replacements = {
        "YEARS || TERM = '__FALL_TERM__'": "YEARS = __FALL_YEAR__ AND TERM = '__FALL_TERM_CODE__'",
        "YEARS || TERM = '__PRIOR_FALL_TERM__'": "YEARS = __PRIOR_FALL_YEAR__ AND TERM = '__FALL_TERM_CODE__'",
        "YEARS || TERM = '__ADMISSION_FALL_TERM__'": "YEARS = __ADMISSION_FALL_YEAR__ AND TERM = '__FALL_TERM_CODE__'",
        "A.YEARS || A.TERM = '__FALL_TERM__'": "A.YEARS = __FALL_YEAR__ AND A.TERM = '__FALL_TERM_CODE__'",
        "A.YEARS || A.TERM = '__ADMISSION_FALL_TERM__'": "A.YEARS = __ADMISSION_FALL_YEAR__ AND A.TERM = '__FALL_TERM_CODE__'",
        "T1.YEARS || T1.TERM = '__GRAD_RATE_COHORT_CURRENT__'": "T1.YEARS = __GRAD_RATE_COHORT_CURRENT_YEAR__ AND T1.TERM = '__FALL_TERM_CODE__'",
        "T1.YEARS || T1.TERM = '__GRAD_RATE_COHORT_PRIOR__'": "T1.YEARS = __GRAD_RATE_COHORT_PRIOR_YEAR__ AND T1.TERM = '__FALL_TERM_CODE__'",
        "YEARS || TERM IN (__DEGREE_AWARD_TERMS_SQL_LIST__)": "(CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)) IN (__DEGREE_AWARD_TERMS_SQL_LIST__)",
        "T1.YEARS || T1.TERM IN (__DEGREE_AWARD_TERMS_SQL_LIST__)": "(CAST(T1.YEARS AS INT) * 10 + CAST(T1.TERM AS INT)) IN (__DEGREE_AWARD_TERMS_SQL_LIST__)",
        "YEARS || TERM AS year_term": "CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) AS year_term",
        "A.YEARS || A.TERM = E.year_term": "CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = E.year_term",
    }
    rendered = sql
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    return rendered


def _rewrite_deprecated_functions(sql: str) -> str:
    rendered = re.sub(
        r"(?<![A-Za-z0-9_])IRAMASTER\.ETHNICITY(?![A-Za-z0-9_])",
        "production.functions.ira_ethnicity",
        sql,
    )
    return rendered.replace(
        "production.functions.ira_ethnicity(CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, MULTIPLE_RACE_CATEGORY, ETHNIC_CODE_OLD)",
        "production.functions.ira_ethnicity(CITIZENSHIP_CODE, IPEDS_RACE_ETHNICITY_CATEGORY, ETHNIC_CODE_OLD, CAST(YEARS AS INT) * 10 + CAST(TERM AS INT))",
    )


def _rewrite_deprecated_ers_tables(sql: str) -> str:
    table_replacements = {
        "IRAMASTER.ERSS": "production.silver.erss",
        "IRAMASTER.ERSA": "production.silver.ersa",
        "IRAMASTER.ERSD_SUPPLEMENTAL": "production.silver.ersd_supplemental",
        "IRAMASTER.ERSD": "production.silver.ersd",
        "production.silver.ERSD_SUPPLEMENTAL": "production.silver.ersd_supplemental",
        "production.silver.ERSD": "production.silver.ersd",
    }
    rendered = sql
    for old, new in table_replacements.items():
        rendered = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, rendered)
    return rendered


def _infer_source_table(sql: str) -> str:
    match = re.search(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_.]+)", sql, re.IGNORECASE)
    return match.group(1) if match else "cds_query_registry"


def _section_from_query_id(query_id: str) -> str:
    return query_id.removeprefix("Q-")


def _stringify_value(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered if rendered else None


def _value_from_row(row: list[Any], index: int | None) -> str | None:
    if index is None or len(row) <= index:
        return None
    return _stringify_value(row[index])


def _column(column_index: dict[str, int], *names: str) -> int | None:
    for name in names:
        idx = column_index.get(name.lower())
        if idx is not None:
            return idx
    return None


def _match_row(rows: list[list[Any]], idx: int | None, expected: str) -> list[Any] | None:
    if idx is None:
        return None
    for row in rows:
        if len(row) > idx and str(row[idx]).strip().upper() == expected.upper():
            return row
    return None


_B2_FIELDS = {
    "Nonresidents": ("EN_1ST_NONRES_ALIEN_1ST_N", "EN_NONRES_ALIEN_N", "EN_TOT_NONRES_ALIEN_TOT_N"),
    "Visa Non-U.S.": ("EN_1ST_NONRES_ALIEN_1ST_N", "EN_NONRES_ALIEN_N", "EN_TOT_NONRES_ALIEN_TOT_N"),
    "Hispanic/Latino": ("EN_1ST_HISPANIC_ETHNICITY_N", "EN_HISPANIC_ETHNICITY_N", "EN_TOT_HISPANIC_ETHNICITY_N"),
    "Black or African American": ("EN_1ST_BLACK_NONHISPANIC_N", "EN_BLACK_NONHISPANIC_N", "EN_TOT_BLACK_NONHISPANIC_N"),
    "White": ("EN_1ST_WHITE_NONHISPANIC_N", "EN_WHITE_NONHISPANIC_N", "EN_TOT_WHITE_NONHISPANIC_N"),
    "American Indian or Alaska Native": ("EN_1ST_NATIVE_NONHISPANIC_N", "EN_NATIVE_NONHISPANIC_N", "EN_TOT_NATIVE_NONHISPANIC_N"),
    "Asian": ("EN_1ST_ASIAN_NONHISPANIC_N", "EN_ASIAN_NONHISPANIC_N", "EN_TOT_ASIAN_NONHISPANIC_N"),
    "Native Hawaiian or Other Pacific Islander": ("EN_1ST_ISLANDER_NONHISPANIC_N", "EN_ISLANDER_NONHISPANIC_N", "EN_TOT_ISLANDER_NONHISPANIC_N"),
    "Two or More Races": ("EN_1ST_MULTIRACE_NONHISPANIC_N", "EN_MULTIRACE_NONHISPANIC_N", "EN_TOT_MULTIRACE_NONHISPANIC_N"),
    "Unknown": ("EN_1ST_RACE_ETHNICITY_UNKNOWN_N", "EN_RACE_ETHNICITY_UNKNOWN_N", "EN_TOT_RACE_ETHNICITY_UNKNOWN_N"),
    "Total": ("EN_1ST_RACE_ETHNICITY_TOT_N", "EN_RACE_ETHNICITY_TOT_N", "EN_TOT_RACE_ETHNICITY_TOT_N"),
}


def _map_b2(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    row_idx = _column(column_index, "race_ethnicity")
    value_columns = ("first_time_first_year_n", "degree_seeking_ug_n", "total_ug_n")
    matching_labels = [label for label, fields in _B2_FIELDS.items() if field_name in fields]
    if not matching_labels:
        return None
    field_index = _B2_FIELDS[matching_labels[0]].index(field_name)
    value_idx = _column(column_index, value_columns[field_index])
    for label in matching_labels:
        row = _match_row(rows, row_idx, label)
        if row:
            return _value_from_row(row, value_idx) or "0"
    return "0"


_C1_FIELD_MAP = {
    "AP_RECD_1ST_MEN_N": ("sex", "MEN", "applied_n"),
    "AP_RECD_1ST_WMN_N": ("sex", "WMN", "applied_n"),
    "AP_RECD_1ST_UNK_N": ("sex", "UNK", "applied_n"),
    "AP_ADMT_1ST_MEN_N": ("sex", "MEN", "admitted_n"),
    "AP_ADMT_1ST_WMN_N": ("sex", "WMN", "admitted_n"),
    "AP_ADMT_1ST_UNK_N": ("sex", "UNK", "admitted_n"),
    "EN_TOT_1ST_MEN_N": ("sex", "MEN", "enrolled_n"),
    "EN_TOT_1ST_WMN_N": ("sex", "WMN", "enrolled_n"),
    "EN_TOT_1ST_UNK_N": ("sex", "UNK", "enrolled_n"),
    "EN_TOT_1ST_FT_MEN_N": ("sex", "MEN", "enrolled_ft_n"),
    "EN_TOT_1ST_PT_MEN_N": ("sex", "MEN", "enrolled_pt_n"),
    "EN_TOT_1ST_FT_WMN_N": ("sex", "WMN", "enrolled_ft_n"),
    "EN_TOT_1ST_PT_WMN_N": ("sex", "WMN", "enrolled_pt_n"),
    "EN_TOT_1ST_FT_UNK_N": ("sex", "UNK", "enrolled_ft_n"),
    "EN_TOT_1ST_PT_UNK_N": ("sex", "UNK", "enrolled_pt_n"),
    "AP_RECD_STATE_1ST_N": ("residency", "STATE", "applied_n"),
    "AP_RECD_NRES_1ST_N": ("residency", "NRES", "applied_n"),
    "AP_RECD_INTL_1ST_N": ("residency", "INTL", "applied_n"),
    "AP_RECD_UNK_1ST_N": ("residency", "UNK", "applied_n"),
    "AP_RECD_1ST_N": ("total", "TOTAL", "applied_n"),
    "AP_ADMT_STATE_1ST_N": ("residency", "STATE", "admitted_n"),
    "AP_ADMT_NRES_1ST_N": ("residency", "NRES", "admitted_n"),
    "AP_ADMT_INTL_1ST_N": ("residency", "INTL", "admitted_n"),
    "AP_ADMT_UNK_1ST_N": ("residency", "UNK", "admitted_n"),
    "AP_ADMT_1ST_N": ("total", "TOTAL", "admitted_n"),
    "EN_TOT_STATE_1ST_N": ("residency", "STATE", "enrolled_n"),
    "EN_TOT_NRES_1ST_N": ("residency", "NRES", "enrolled_n"),
    "EN_TOT_INTL_1ST_N": ("residency", "INTL", "enrolled_n"),
    "EN_TOT_UNK_1ST_N": ("residency", "UNK", "enrolled_n"),
    "EN_TOT_1ST_N": ("total", "TOTAL", "enrolled_n"),
}


def _map_c1(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    mapping = _C1_FIELD_MAP.get(field_name)
    if not mapping:
        return None
    expected_dimension, expected_bucket, value_column = mapping
    dim_idx = _column(column_index, "dimension")
    bucket_idx = _column(column_index, "bucket")
    value_idx = _column(column_index, value_column)
    for row in rows:
        if len(row) > max(dim_idx or 0, bucket_idx or 0):
            if str(row[dim_idx or 0]).strip().lower() == expected_dimension and str(row[bucket_idx or 0]).strip().upper() == expected_bucket:
                return _value_from_row(row, value_idx)
    return "0"


_D2_FIELD_MAP = {
    "AP_TFER_MEN_N": ("MEN", "applicants"),
    "AP_TFER_WMN_N": ("WMN", "applicants"),
    "AP_TFER_UNK_N": ("UNK", "applicants"),
    "AP_TFER_N": ("TOTAL", "applicants"),
    "AD_TFER_MEN_N": ("MEN", "admitted"),
    "AD_TFER_WMN_N": ("WMN", "admitted"),
    "AD_TFER_UNK_N": ("UNK", "admitted"),
    "AD_TFER_N": ("TOTAL", "admitted"),
    "EN_TFER_MEN_N": ("MEN", "enrolled"),
    "EN_TFER_WMN_N": ("WMN", "enrolled"),
    "EN_TFER_UNK_N": ("UNK", "enrolled"),
    "EN_TFER_N": ("TOTAL", "enrolled"),
}


def _map_d2(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    mapping = _D2_FIELD_MAP.get(field_name)
    if not mapping:
        return None
    bucket, value_column = mapping
    row = _match_row(rows, _column(column_index, "sex_bucket"), bucket)
    return _value_from_row(row or [], _column(column_index, value_column)) or "0"


_GPA_FIELD_MAP = {
    **{f"FRSH_GPA_SUBMIT_{idx}_P": (str(idx), "pct_of_submitters") for idx in range(1, 10)},
    **{f"EN_FRSH_GPA_{idx}_P": (str(idx), "pct_of_all_enrolled") for idx in range(1, 10)},
    **{f"FRSH_GPA_NO_SUB_{idx}_P": (str(idx), "pct_of_all_enrolled") for idx in range(1, 10)},
    "TOT_FRSH_GPA_SUBMIT_P": ("TOTAL_SUBMITTED", "pct_of_submitters"),
    "TOT_EN_FRSH_GPA_P": ("TOTAL_SUBMITTED", "pct_of_all_enrolled"),
    "FRSH_GPA": ("AVG_HSGPA", "avg_gpa"),
}


def _map_gpa(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    mapping = _GPA_FIELD_MAP.get(field_name)
    if not mapping:
        return None
    bucket, value_column = mapping
    row = _match_row(rows, _column(column_index, "gpa_bin"), bucket)
    value = _value_from_row(row or [], _column(column_index, value_column))
    if value is None and bucket != "AVG_HSGPA":
        return "0"
    return value


_I1_FIELD_MAP = {
    "FT_N": ("TOTAL", "ft_n"),
    "PT_N": ("TOTAL", "pt_n"),
    "TOT_N": ("TOTAL", "total_n"),
    "MIN_FT_N": ("MINORITY", "ft_n"),
    "MIN_PT_N": ("MINORITY", "pt_n"),
    "MIN_TOT_N": ("MINORITY", "total_n"),
    "FT_WMN_N": ("WOMEN", "ft_n"),
    "PT_WMN_N": ("WOMEN", "pt_n"),
    "TOT_WMN_N": ("WOMEN", "total_n"),
    "FT_MEN_N": ("MEN", "ft_n"),
    "PT_MEN_N": ("MEN", "pt_n"),
    "TOT_MEN_N": ("MEN", "total_n"),
    "NRES_FT_N": ("NONRESIDENT", "ft_n"),
    "NRES_PT_N": ("NONRESIDENT", "pt_n"),
    "NRES_TOT_N": ("NONRESIDENT", "total_n"),
    "FT_DEG_TERM_N": ("TERMINAL_DEGREE", "ft_n"),
    "PT_DEG_TERM_N": ("TERMINAL_DEGREE", "pt_n"),
    "TOT_DEG_TERM_N": ("TERMINAL_DEGREE", "total_n"),
    "MASTER_FT_N": ("MASTERS_NON_TERMINAL", "ft_n"),
    "MASTER_PT_N": ("MASTERS_NON_TERMINAL", "pt_n"),
    "MASTER_TOT_N": ("MASTERS_NON_TERMINAL", "total_n"),
    "BACH_FT_N": ("BACHELORS", "ft_n"),
    "BACH_PT_N": ("BACHELORS", "pt_n"),
    "BACH_TOT_N": ("BACHELORS", "total_n"),
    "UNKNOWN_FT_N": ("OTHER_UNKNOWN", "ft_n"),
    "UNKNOWN_PT_N": ("OTHER_UNKNOWN", "pt_n"),
    "UNKNOWN_TOT_N": ("OTHER_UNKNOWN", "total_n"),
    "GRAD_FT_N": ("GRAD_ONLY", "ft_n"),
    "GRAD_PT_N": ("GRAD_ONLY", "pt_n"),
    "GRAD_TOT_N": ("GRAD_ONLY", "total_n"),
}


def _map_i1(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    mapping = _I1_FIELD_MAP.get(field_name)
    if not mapping:
        return None
    metric, value_column = mapping
    row = _match_row(rows, _column(column_index, "metric"), metric)
    return _value_from_row(row or [], _column(column_index, value_column)) or "0"


_GRAD_METRICS = {
    "GRS_BACH_INIT": "init_n",
    "GRS_BACH_EXCLUDE": "exclude_n",
    "GRS_BACH_ADJUST": "adjust_n",
    "GRS_4YR": "grad_4yr_n",
    "GRS_5YR": "grad_5yr_n",
    "GRS_6YR": "grad_6yr_n",
    "GRS_BACH_TOT": "grad_total_n",
    "GRS_BACH": "grad_total_n",
}


def _map_grad_rates(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    name = field_name.removeprefix("GRS_LY_")
    bucket = "TOTAL"
    if name.endswith("_PELL_N") or name.endswith("_PELL_P"):
        bucket = "PELL"
    elif name.endswith("_STAFFORD_N") or name.endswith("_STAFFORD_P"):
        bucket = "STAFFORD"
    elif name.endswith("_NO_AID_N") or name.endswith("_NO_AID_P"):
        bucket = "NO_AID"
    metric = "grad_rate_p" if name.endswith("_P") else None
    if metric is None:
        for prefix, column in sorted(_GRAD_METRICS.items(), key=lambda item: len(item[0]), reverse=True):
            if name.startswith(prefix):
                metric = column
                break
    if metric is None:
        return None
    row = _match_row(rows, _column(column_index, "aid_bucket"), bucket)
    return _value_from_row(row or [], _column(column_index, metric))



_B1_DETAIL_ROWS = ("FRSH", "OTH_1ST", "DEG", "CRDT", "GRAD")


def _b1_count_lookup(column_index: dict[str, int], rows: list[list[Any]]) -> dict[tuple[str, str, str], int]:
    row_idx = _column(column_index, "row_bucket")
    load_idx = _column(column_index, "load_bucket")
    sex_idx = _column(column_index, "sex_bucket")
    value_idx = _column(column_index, "value")
    if None in (row_idx, load_idx, sex_idx, value_idx):
        return {}
    lookup: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if len(row) <= max(row_idx, load_idx, sex_idx, value_idx):
            continue
        key = (
            str(row[row_idx]).strip().upper(),
            str(row[load_idx]).strip().upper(),
            str(row[sex_idx]).strip().upper(),
        )
        lookup[key] = int(float(row[value_idx] or 0))
    return lookup


def _parse_b1_detail_field(field_name: str) -> tuple[str, str, str] | None:
    match = re.match(
        r"^(?:CDS_)?EN_(FRSH|OTH_1ST|DEG|CRDT|UG|GRAD_DEG|GRAD_OTH|GRAD_CRDT|GRAD|TOT_DEG|TOT)_(FT|PT)_(MEN|WMN|UNK)_N$",
        field_name,
    )
    if not match:
        return None
    row_bucket, load_bucket, sex_bucket = match.groups()
    return row_bucket, load_bucket, sex_bucket


def _b1_bucket_value(
    lookup: dict[tuple[str, str, str], int],
    row_bucket: str,
    load_bucket: str,
    sex_bucket: str,
) -> int:
    if row_bucket == "TOT_DEG":
        return sum(_b1_bucket_value(lookup, row, load_bucket, sex_bucket) for row in ("FRSH", "OTH_1ST", "DEG"))
    if row_bucket == "UG":
        return _b1_bucket_value(lookup, "TOT_DEG", load_bucket, sex_bucket) + lookup.get(
            ("CRDT", load_bucket, sex_bucket), 0
        )
    if row_bucket == "GRAD":
        grad_total = sum(lookup.get((row, load_bucket, sex_bucket), 0) for row in ("GRAD_DEG", "GRAD_OTH", "GRAD_CRDT"))
        return grad_total or lookup.get(("GRAD", load_bucket, sex_bucket), 0)
    if row_bucket == "GRAD_DEG" and lookup.get(("GRAD_DEG", load_bucket, sex_bucket), 0) == 0:
        return lookup.get(("GRAD", load_bucket, sex_bucket), 0)
    if row_bucket == "TOT":
        return _b1_bucket_value(lookup, "UG", load_bucket, sex_bucket) + _b1_bucket_value(
            lookup, "GRAD", load_bucket, sex_bucket
        )
    return lookup.get((row_bucket, load_bucket, sex_bucket), 0)


def _map_b1(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    lookup = _b1_count_lookup(column_index, rows)
    if not lookup:
        return None

    parsed = _parse_b1_detail_field(field_name)
    if parsed:
        value = _b1_bucket_value(lookup, *parsed)
        return str(value)

    if field_name == "EN_TOT_UG_N":
        total = sum(_b1_bucket_value(lookup, "UG", load, sex) for load in ("FT", "PT") for sex in ("MEN", "WMN", "UNK"))
        return str(total)
    if field_name == "EN_TOT_GRAD_N":
        total = sum(_b1_bucket_value(lookup, "GRAD", load, sex) for load in ("FT", "PT") for sex in ("MEN", "WMN", "UNK"))
        return str(total)
    if field_name == "EN_TOT_N":
        ug = int(_map_b1("EN_TOT_UG_N", column_index, rows) or 0)
        grad = int(_map_b1("EN_TOT_GRAD_N", column_index, rows) or 0)
        return str(ug + grad)
    return None


_B3_FIELDS = {
    "CERTIF_DIPLOMA_N", "DEG_ASSOC_N", "DEG_BACH_N", "CERTIF_POST_BACH_N",
    "DEG_MASTER_N", "CERTIF_POST_MASTER_N", "DEG_DOCTOR_RESEARCH_N",
    "DEG_DOCTOR_PROF_N", "DEG_DOCTOR_OTH_N",
}


def _map_b3(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    if field_name not in _B3_FIELDS:
        return None
    field_idx = column_index.get("pdf_field")
    value_idx = column_index.get("value")
    if field_idx is None or value_idx is None:
        return None
    for row in rows:
        if len(row) > max(field_idx, value_idx) and normalize_pdf_field_name(str(row[field_idx])) == normalize_pdf_field_name(field_name):
            return _stringify_value(row[value_idx]) or "0"
    return "0"


def _map_j(field_name: str, column_index: dict[str, int], rows: list[list[Any]]) -> str | None:
    if field_name in {"CERTIF_P_TOT_P", "ASSOC_TOT_P", "BACH_TOT_P"}:
        return "100"
    return None

_DIMENSIONAL_MAPPERS = {
    "Q-B1": _map_b1,
    "Q-B2": _map_b2,
    "Q-B3": _map_b3,
    "Q-C1-C2": _map_c1,
    "Q-C11-C12-C13": _map_gpa,
    "Q-D2": _map_d2,
    "Q-B4-B11": _map_grad_rates,
    "Q-I1": _map_i1,
    "Q-J": _map_j,
}


_FIELD_OVERRIDES = {
    "Q-B2": {field for fields in _B2_FIELDS.values() for field in fields},
    "Q-C1-C2": set(_C1_FIELD_MAP),
    "Q-C11-C12-C13": set(_GPA_FIELD_MAP),
    "Q-D2": set(_D2_FIELD_MAP),
    "Q-I1": set(_I1_FIELD_MAP),
}


_FALLBACK_REGISTRY_MARKDOWN = """
## Q-B22: Retention Rate

**PDF fields filled:** `RETENTION_FRSH_N`, `RETENTION_ENROLL_N`, `RETENTION_FRSH_P`.

**SQL:**

```sql
WITH returned AS (
  SELECT DISTINCT EMPLID AS returned_emplid
  FROM IRAMASTER.ERSS
  WHERE YEARS || TERM = '${fall_term}'
    AND STUDENT_LEVEL_CODE < 5
), cohort AS (
  SELECT DISTINCT EMPLID
  FROM IRAMASTER.ERSS
  WHERE YEARS || TERM = '${prior_fall_term}'
    AND STUDENT_LEVEL_CODE < 5
    AND ENROLLMENT_STATUS = 5
    AND (TUA_LOWER_DIVISION + TUA_UPPER_DIVISION + TUA_GRADUATE + TUA_PRE_COLLEGIATE) >= ${full_time_credit_threshold}
)
SELECT 'RETENTION_FRSH_N' AS pdf_field, CAST(COUNT(DISTINCT C.EMPLID) AS STRING) AS value FROM cohort C
UNION ALL
SELECT 'RETENTION_ENROLL_N', CAST(COUNT(DISTINCT R.returned_emplid) AS STRING)
FROM cohort C LEFT JOIN returned R ON C.EMPLID = R.returned_emplid
UNION ALL
SELECT 'RETENTION_FRSH_P', CAST(ROUND(COUNT(DISTINCT R.returned_emplid) / NULLIF(COUNT(DISTINCT C.EMPLID),0) * 100, 2) AS STRING)
FROM cohort C LEFT JOIN returned R ON C.EMPLID = R.returned_emplid;
```
"""
