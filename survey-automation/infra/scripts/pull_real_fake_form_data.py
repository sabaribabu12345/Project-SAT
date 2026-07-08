from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from databricks.sdk.service.sql import StatementState


SURVEY_AUTOMATION_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SURVEY_AUTOMATION_ROOT))
os.chdir(SURVEY_AUTOMATION_ROOT)

from apps.api.databricks_resolver import DatabricksSqlValueReader  # noqa: E402
from apps.api.settings import get_settings  # noqa: E402
from infra.scripts.validate_fake_form_data import validate_payload  # noqa: E402


ENROLLMENT_TABLE = "production.silver.erss"
ADMISSIONS_TABLE = "production.silver.ersa"
FALL_TERM = "4"

GENDERS = ("men", "women", "other")
UNDERGRAD_CATEGORIES = ("ftf", "otherfy", "allother")
TIME_STATUSES = ("ft", "pt")


def select_baseline_input_path(generated_path: Path) -> Path:
    if generated_path.exists():
        return generated_path
    example_path = generated_path.with_name("fake-survey-form-data.example.json")
    if example_path.exists():
        return example_path
    return generated_path


def execute_sql(
    sql: str,
    row_limit: int = 1000,
    max_wait_seconds: int | None = None,
    max_attempts: int = 3,
) -> tuple[list[str], list[list[Any]]]:
    settings = get_settings()
    reader = DatabricksSqlValueReader(settings)
    client = reader._client_for_settings()
    if not settings.databricks_sql_warehouse_id:
        raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is required")
    wait_seconds = max_wait_seconds if max_wait_seconds is not None else settings.databricks_sql_poll_timeout_seconds

    terminal_states = {StatementState.CANCELED, StatementState.CLOSED, StatementState.FAILED, StatementState.SUCCEEDED}
    last_timeout: TimeoutError | None = None

    for attempt in range(1, max_attempts + 1):
        response = client.statement_execution.execute_statement(
            statement=sql,
            warehouse_id=settings.databricks_sql_warehouse_id,
            row_limit=row_limit,
            wait_timeout=settings.databricks_sql_wait_timeout,
        )
        deadline = time.monotonic() + wait_seconds
        while response.status and response.status.state not in terminal_states:
            if time.monotonic() >= deadline:
                last_timeout = TimeoutError(
                    f"Databricks statement timed out (attempt {attempt}/{max_attempts}): {response.statement_id}"
                )
                break
            time.sleep(1)
            if not response.statement_id:
                raise RuntimeError("Databricks statement response missing statement_id")
            response = client.statement_execution.get_statement(response.statement_id)

        if last_timeout and response.status and response.status.state not in terminal_states:
            if attempt < max_attempts:
                time.sleep(min(3 * attempt, 10))
                continue
            raise last_timeout

        if not response.status or response.status.state != StatementState.SUCCEEDED:
            error = response.status.error.message if response.status and response.status.error else "unknown"
            raise RuntimeError(f"Databricks statement failed: {error}")

        columns = [col.name for col in (response.manifest.schema.columns if response.manifest and response.manifest.schema else [])]
        rows = response.result.data_array if response.result and response.result.data_array else []
        return columns, rows

    raise RuntimeError("Databricks query failed unexpectedly after retry loop")


def latest_fall_year(max_wait_seconds: int, max_attempts: int) -> int:
    _, rows = execute_sql(
        f"""
        SELECT MAX(YEARS) AS survey_year
        FROM {ENROLLMENT_TABLE}
        WHERE TERM = '{FALL_TERM}'
        """,
        row_limit=1,
        max_wait_seconds=max_wait_seconds,
        max_attempts=max_attempts,
    )
    if not rows or rows[0][0] is None:
        raise RuntimeError(f"No Fall term rows found in {ENROLLMENT_TABLE}")
    return int(float(rows[0][0]))


def gender_bucket_sql() -> str:
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


def term_units_sql() -> str:
    return "COALESCE(TUA_LOWER_DIVISION, 0) + COALESCE(TUA_UPPER_DIVISION, 0) + COALESCE(TUA_GRADUATE, 0)"


def pull_enrollment_counts(
    year: int,
    max_wait_seconds: int,
    max_attempts: int,
) -> dict[tuple[str, str, str], int]:
    gender_bucket = gender_bucket_sql()
    term_units = term_units_sql()
    _, rows = execute_sql(
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
        WHERE YEARS = {year}
          AND TERM = '{FALL_TERM}'
        GROUP BY enrollment_category, time_status, gender_bucket
        ORDER BY enrollment_category, time_status, gender_bucket
        """,
        row_limit=200,
        max_wait_seconds=max_wait_seconds,
        max_attempts=max_attempts,
    )
    result: dict[tuple[str, str, str], int] = {}
    for row in rows:
        category, time_status, gender, count = row
        if category == "unknown":
            continue
        result[(str(category), str(time_status), str(gender))] = int(count or 0)
    return result


def pull_admissions_counts(
    year: int,
    max_wait_seconds: int,
    max_attempts: int,
) -> dict[tuple[str, str], int]:
    gender_bucket = gender_bucket_sql()
    _, rows = execute_sql(
        f"""
        SELECT metric, gender_bucket, COUNT(*) AS headcount
        FROM (
          SELECT
            'applied' AS metric,
            {gender_bucket} AS gender_bucket
          FROM {ADMISSIONS_TABLE}
          WHERE YEARS = {year}
            AND TERM = '{FALL_TERM}'
            AND STUDENT_LEVEL_CODE = '1'

          UNION ALL

          SELECT
            'admitted' AS metric,
            {gender_bucket} AS gender_bucket
          FROM {ADMISSIONS_TABLE}
          WHERE YEARS = {year}
            AND TERM = '{FALL_TERM}'
            AND STUDENT_LEVEL_CODE = '1'
            AND ADMISSION_STATUS IN ('A', 'N')
        )
        GROUP BY metric, gender_bucket
        ORDER BY metric, gender_bucket
        """,
        row_limit=100,
        max_wait_seconds=max_wait_seconds,
        max_attempts=max_attempts,
    )
    return {(str(metric), str(gender)): int(count or 0) for metric, gender, count in rows}


def apply_real_enrollment(data: dict[str, Any], counts: dict[tuple[str, str, str], int], year: int) -> dict[str, Any]:
    def value(category: str, time_status: str, gender: str) -> int:
        return counts.get((category, time_status, gender), 0)

    for category in UNDERGRAD_CATEGORIES:
        for gender in GENDERS:
            data[f"ft_{category}_{gender}"] = str(value(category, "ft", gender))
            data[f"pt_{category}_{gender}"] = str(value(category, "pt", gender))

    for gender in GENDERS:
        data[f"ft_total_ug_{gender}"] = str(
            sum(value(category, "ft", gender) for category in UNDERGRAD_CATEGORIES)
        )
        data[f"pt_total_ug_{gender}"] = str(
            sum(value(category, "pt", gender) for category in UNDERGRAD_CATEGORIES)
        )
        data[f"ft_total_grad_{gender}"] = str(value("grad", "ft", gender))
        data[f"pt_total_grad_{gender}"] = str(value("grad", "pt", gender))
        data[f"enrolled_{gender}"] = str(value("ftf", "ft", gender) + value("ftf", "pt", gender))

    data["enrolled_total"] = str(sum(int(data[f"enrolled_{gender}"]) for gender in GENDERS))
    data["total_undergraduates"] = str(
        sum(
            int(data[f"{time_status}_total_ug_{gender}"])
            for time_status in TIME_STATUSES
            for gender in GENDERS
        )
    )
    data["total_graduates"] = str(
        sum(
            int(data[f"{time_status}_total_grad_{gender}"])
            for time_status in TIME_STATUSES
            for gender in GENDERS
        )
    )
    data["grand_total_enrollment"] = str(int(data["total_undergraduates"]) + int(data["total_graduates"]))
    data.setdefault("__meta", {})
    data["__meta"].update(
        {
            "realDataPulledAt": datetime.now(UTC).isoformat(),
            "realDataSurveyYear": year,
            "realDataTerm": FALL_TERM,
            "realDataSources": [ENROLLMENT_TABLE, ADMISSIONS_TABLE],
            "realDataNotes": (
                "Enrollment fields are pulled from production.silver.erss. Gender buckets use the term-aware "
                "SEX_CODE/GENDER_IDENTITY_CODE mapping provided by IRA. Undergraduate full-time is derived as "
                "12 or more term units; graduate/postbaccalaureate full-time is derived as 9 or more term units. "
                "Admissions applied/admitted fields are pulled from production.silver.ersa; admitted uses "
                "ADMISSION_STATUS in A/N and should be confirmed with IRA if a stricter admitted rule is needed."
            ),
        }
    )
    return data


def apply_real_admissions(data: dict[str, Any], counts: dict[tuple[str, str], int]) -> dict[str, Any]:
    for metric in ("applied", "admitted"):
        for gender in GENDERS:
            data[f"{metric}_{gender}"] = str(counts.get((metric, gender), 0))
        data[f"{metric}_total"] = str(sum(int(data[f"{metric}_{gender}"]) for gender in GENDERS))
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull real Databricks census data into the fake survey form payload.")
    parser.add_argument("--year", type=int, default=None, help="Fall census year to pull. Defaults to latest Fall term.")
    parser.add_argument(
        "--input",
        type=Path,
        default=select_baseline_input_path(PROJECT_ROOT / "fake-survey-form" / "fake-survey-form-data.json"),
        help="Baseline fake form JSON used for fields without Databricks mappings.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "fake-survey-form" / "fake-survey-form-data.json",
        help="Root output JSON path.",
    )
    parser.add_argument(
        "--static-output",
        type=Path,
        default=PROJECT_ROOT / "fake-survey-form" / "fake-survey-form-data.json",
        help="Static JSON path served by the fake-form nginx container.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run consistency checks on the generated output and fail if mismatched.",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=600,
        help="Max seconds to wait per Databricks statement attempt (default: 600).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="How many times to retry a timed-out Databricks statement (default: 3).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    year = args.year or latest_fall_year(args.max_wait_seconds, args.max_attempts)
    data = json.loads(args.input.read_text())

    enrollment_counts = pull_enrollment_counts(year, args.max_wait_seconds, args.max_attempts)
    admissions_counts = pull_admissions_counts(year, args.max_wait_seconds, args.max_attempts)
    data = apply_real_enrollment(data, enrollment_counts, year)
    data = apply_real_admissions(data, admissions_counts)

    rendered = json.dumps(data, indent=2) + "\n"
    args.output.write_text(rendered)
    args.static_output.write_text(rendered)

    artifact = SURVEY_AUTOMATION_ROOT / "artifacts" / f"fake-form-real-data-{year}-fall.json"
    artifact.write_text(rendered)

    if args.validate:
        ok, errors = validate_payload(args.output)
        if not ok:
            raise RuntimeError(f"Validation failed for {args.output}: {'; '.join(errors)}")

    print(json.dumps(
        {
            "survey_year": year,
            "term": FALL_TERM,
            "output": str(args.output),
            "static_output": str(args.static_output),
            "artifact": str(artifact),
            "validated": args.validate,
            "applied_total": data["applied_total"],
            "enrolled_total": data["enrolled_total"],
            "total_undergraduates": data["total_undergraduates"],
            "total_graduates": data["total_graduates"],
            "grand_total_enrollment": data["grand_total_enrollment"],
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
