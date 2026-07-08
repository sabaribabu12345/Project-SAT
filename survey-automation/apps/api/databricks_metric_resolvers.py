from __future__ import annotations

from typing import Any, Protocol


ENROLLMENT_TABLE = "production.silver.erss"
ADMISSIONS_TABLE = "production.silver.ersa"
FALL_TERM = "4"

GENDERS = ("men", "women", "other")
UNDERGRAD_CATEGORIES = ("ftf", "otherfy", "allother")
TIME_STATUSES = ("ft", "pt")


class DatabricksRowsReader(Protocol):
    def query_rows(self, sql: str, *, row_limit: int = 1000) -> tuple[list[str], list[list[Any]]]:
        ...


def resolve_metric_group(
    *,
    resolver_name: str,
    survey_year: int,
    sql_reader: DatabricksRowsReader,
) -> dict[str, str]:
    if resolver_name == "fall_enrollment_counts":
        return _resolve_fall_enrollment_counts(survey_year=survey_year, sql_reader=sql_reader)
    if resolver_name == "fall_admissions_counts":
        return _resolve_fall_admissions_counts(survey_year=survey_year, sql_reader=sql_reader)
    raise ValueError(f"Unknown resolver_name: {resolver_name}")


def resolver_name_for_fake_form_field(field_key: str) -> str | None:
    enrollment_fields = _fall_enrollment_field_names()
    admissions_fields = _fall_admissions_field_names()
    if field_key in enrollment_fields:
        return "fall_enrollment_counts"
    if field_key in admissions_fields:
        return "fall_admissions_counts"
    return None


def resolver_sources(resolver_name: str) -> list[str]:
    if resolver_name == "fall_enrollment_counts":
        return [ENROLLMENT_TABLE]
    if resolver_name == "fall_admissions_counts":
        return [ADMISSIONS_TABLE]
    return []


def _resolve_fall_enrollment_counts(
    *,
    survey_year: int,
    sql_reader: DatabricksRowsReader,
) -> dict[str, str]:
    counts = _pull_enrollment_counts(survey_year=survey_year, sql_reader=sql_reader)
    values: dict[str, str] = {}

    def value(category: str, time_status: str, gender: str) -> int:
        return counts.get((category, time_status, gender), 0)

    for category in UNDERGRAD_CATEGORIES:
        for gender in GENDERS:
            values[f"ft_{category}_{gender}"] = str(value(category, "ft", gender))
            values[f"pt_{category}_{gender}"] = str(value(category, "pt", gender))

    for gender in GENDERS:
        values[f"ft_total_ug_{gender}"] = str(
            sum(value(category, "ft", gender) for category in UNDERGRAD_CATEGORIES)
        )
        values[f"pt_total_ug_{gender}"] = str(
            sum(value(category, "pt", gender) for category in UNDERGRAD_CATEGORIES)
        )
        values[f"ft_total_grad_{gender}"] = str(value("grad", "ft", gender))
        values[f"pt_total_grad_{gender}"] = str(value("grad", "pt", gender))
        values[f"enrolled_{gender}"] = str(value("ftf", "ft", gender) + value("ftf", "pt", gender))

    values["enrolled_total"] = str(sum(int(values[f"enrolled_{gender}"]) for gender in GENDERS))
    values["total_undergraduates"] = str(
        sum(
            int(values[f"{time_status}_total_ug_{gender}"])
            for time_status in TIME_STATUSES
            for gender in GENDERS
        )
    )
    values["total_graduates"] = str(
        sum(
            int(values[f"{time_status}_total_grad_{gender}"])
            for time_status in TIME_STATUSES
            for gender in GENDERS
        )
    )
    values["grand_total_enrollment"] = str(int(values["total_undergraduates"]) + int(values["total_graduates"]))
    return values


def _resolve_fall_admissions_counts(
    *,
    survey_year: int,
    sql_reader: DatabricksRowsReader,
) -> dict[str, str]:
    counts = _pull_admissions_counts(survey_year=survey_year, sql_reader=sql_reader)
    values: dict[str, str] = {}
    for metric in ("applied", "admitted"):
        for gender in GENDERS:
            values[f"{metric}_{gender}"] = str(counts.get((metric, gender), 0))
        values[f"{metric}_total"] = str(sum(int(values[f"{metric}_{gender}"]) for gender in GENDERS))
    return values


def _pull_enrollment_counts(
    *,
    survey_year: int,
    sql_reader: DatabricksRowsReader,
) -> dict[tuple[str, str, str], int]:
    gender_bucket = _gender_bucket_sql()
    term_units = _term_units_sql()
    _, rows = sql_reader.query_rows(
        f"""
        SELECT
          CASE
            WHEN STUDENT_LEVEL_CODE IN ('1', '2', '3', '4') AND ENROLLMENT_STATUS = '5' THEN 'ftf'
            WHEN STUDENT_LEVEL_CODE = '1' THEN 'otherfy'
            WHEN STUDENT_LEVEL_CODE IN ('2', '3', '4') THEN 'allother'
            WHEN STUDENT_LEVEL_CODE = '5' THEN 'grad'
            ELSE 'unknown'
          END AS enrollment_category,
          CASE
            WHEN {term_units} >= CASE WHEN STUDENT_LEVEL_CODE = '5' THEN 9 ELSE 12 END THEN 'ft'
            ELSE 'pt'
          END AS time_status,
          {gender_bucket} AS gender_bucket,
          COUNT(*) AS headcount
        FROM {ENROLLMENT_TABLE}
        WHERE YEARS = {int(survey_year)}
          AND TERM = '{FALL_TERM}'
        GROUP BY enrollment_category, time_status, gender_bucket
        ORDER BY enrollment_category, time_status, gender_bucket
        """,
        row_limit=200,
    )
    result: dict[tuple[str, str, str], int] = {}
    for category, time_status, gender, count in rows:
        if category == "unknown":
            continue
        result[(str(category), str(time_status), str(gender))] = int(count or 0)
    return result


def _pull_admissions_counts(
    *,
    survey_year: int,
    sql_reader: DatabricksRowsReader,
) -> dict[tuple[str, str], int]:
    gender_bucket = _gender_bucket_sql()
    _, rows = sql_reader.query_rows(
        f"""
        SELECT metric, gender_bucket, COUNT(*) AS headcount
        FROM (
          SELECT
            'applied' AS metric,
            {gender_bucket} AS gender_bucket
          FROM {ADMISSIONS_TABLE}
          WHERE YEARS = {int(survey_year)}
            AND TERM = '{FALL_TERM}'
            AND STUDENT_LEVEL_CODE = '1'

          UNION ALL

          SELECT
            'admitted' AS metric,
            {gender_bucket} AS gender_bucket
          FROM {ADMISSIONS_TABLE}
          WHERE YEARS = {int(survey_year)}
            AND TERM = '{FALL_TERM}'
            AND STUDENT_LEVEL_CODE = '1'
            AND ADMISSION_STATUS IN ('A', 'N')
        )
        GROUP BY metric, gender_bucket
        ORDER BY metric, gender_bucket
        """,
        row_limit=100,
    )
    return {(str(metric), str(gender)): int(count or 0) for metric, gender, count in rows}


def _gender_bucket_sql() -> str:
    gender_label = """
    CASE
      WHEN CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) < 20254 THEN
        CASE
          WHEN SEX_CODE = 'F' THEN 'Female'
          WHEN SEX_CODE = 'M' THEN 'Male'
          WHEN SEX_CODE = 'N' THEN 'Nonbinary'
          WHEN SEX_CODE = 'U' THEN 'Unknown'
          ELSE 'Unknown'
        END
      WHEN CAST(YEARS AS INT) * 10 + CAST(TERM AS INT) >= 20254 THEN
        CASE
          WHEN GENDER_IDENTITY_CODE = '10' THEN 'Man'
          WHEN GENDER_IDENTITY_CODE = '11' THEN 'Woman'
          WHEN GENDER_IDENTITY_CODE = '12' THEN 'Trans Man'
          WHEN GENDER_IDENTITY_CODE = '13' THEN 'Trans Woman'
          WHEN GENDER_IDENTITY_CODE = '14' THEN 'Genderqueer or gender fluid'
          WHEN GENDER_IDENTITY_CODE = '15' THEN 'Not Sure'
          WHEN GENDER_IDENTITY_CODE = '16' THEN 'Decline to State'
          WHEN GENDER_IDENTITY_CODE = '17' THEN 'Another Gender'
          WHEN GENDER_IDENTITY_CODE = '18' THEN 'Nonbinary'
          WHEN SEX_CODE = 'F' THEN 'Female'
          WHEN SEX_CODE = 'M' THEN 'Male'
          WHEN SEX_CODE = 'N' THEN 'Nonbinary'
          WHEN SEX_CODE = 'U' THEN 'Unknown'
          ELSE 'Unknown'
        END
      ELSE CONCAT('Error Invalid Term: ', CAST(YEARS AS STRING), CAST(TERM AS STRING))
    END
    """
    return f"""
    CASE
      WHEN ({gender_label}) IN ('Male', 'Man', 'Trans Man') THEN 'men'
      WHEN ({gender_label}) IN ('Female', 'Woman', 'Trans Woman') THEN 'women'
      ELSE 'other'
    END
    """


def _term_units_sql() -> str:
    return "COALESCE(TUA_LOWER_DIVISION, 0) + COALESCE(TUA_UPPER_DIVISION, 0) + COALESCE(TUA_GRADUATE, 0)"


def _fall_enrollment_field_names() -> set[str]:
    fields: set[str] = set()
    for category in UNDERGRAD_CATEGORIES:
        for gender in GENDERS:
            fields.add(f"ft_{category}_{gender}")
            fields.add(f"pt_{category}_{gender}")
    for gender in GENDERS:
        fields.update(
            {
                f"ft_total_ug_{gender}",
                f"pt_total_ug_{gender}",
                f"ft_total_grad_{gender}",
                f"pt_total_grad_{gender}",
                f"enrolled_{gender}",
            }
        )
    fields.update({"enrolled_total", "total_undergraduates", "total_graduates", "grand_total_enrollment"})
    return fields


def _fall_admissions_field_names() -> set[str]:
    fields: set[str] = set()
    for metric in ("applied", "admitted"):
        for gender in GENDERS:
            fields.add(f"{metric}_{gender}")
        fields.add(f"{metric}_total")
    return fields
