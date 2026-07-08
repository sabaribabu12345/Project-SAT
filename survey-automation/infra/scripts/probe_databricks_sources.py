from __future__ import annotations

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from apps.api.settings import get_settings


SOURCE_TABLES = [
    "bronze.cms.ps_stdnt_aid_atrbt",
    "bronze.cms.ps_stdnt_awards",
    "bronze.cms.ps_stdnt_awd_per",
    "bronze.cms.ps_stdnt_awrd_actv",
    "bronze.cms.ps_stdnt_awrd_disb",
    "bronze.cms.ps_stdnt_fa_term",
    "production.silver.erss",
    "production.silver.ersa",
    "production.silver.serss",
    "production.silver.ersd",
    "production.silver.ersd_supplemental",
    "production.silver.ira_faculty",
    "production.reference.ira_ss_hegis_cip",
]


def _quote_fqn(value: str) -> str:
    return ".".join(f"`{part}`" for part in value.split("."))


def _statement_client() -> tuple[WorkspaceClient, str]:
    settings = get_settings()
    if not settings.databricks_sql_warehouse_id:
        raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is not set")
    return (
        WorkspaceClient(
            host=settings.databricks_host,
            auth_type=settings.databricks_auth_type,
            client_id=settings.databricks_client_id,
            client_secret=settings.databricks_client_secret,
            token=settings.databricks_token,
            product="survey-automation-v3",
            product_version="0.1.0",
        ),
        settings.databricks_sql_warehouse_id,
    )


def _run_sql(client: WorkspaceClient, warehouse_id: str, statement: str, row_limit: int = 100) -> list[list[str]]:
    settings = get_settings()
    terminal = {StatementState.CANCELED, StatementState.CLOSED, StatementState.FAILED, StatementState.SUCCEEDED}
    response = client.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout=settings.databricks_sql_wait_timeout,
        row_limit=row_limit,
    )
    deadline = time.monotonic() + settings.databricks_sql_poll_timeout_seconds
    while response.status and response.status.state not in terminal:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out waiting for statement_id={response.statement_id}")
        time.sleep(2)
        if not response.statement_id:
            raise RuntimeError("Statement response missing statement_id")
        response = client.statement_execution.get_statement(response.statement_id)

    if not response.status or response.status.state != StatementState.SUCCEEDED:
        message = response.status.error.message if response.status and response.status.error else "unknown error"
        raise RuntimeError(message)
    return response.result.data_array if response.result and response.result.data_array else []


def main() -> None:
    client, warehouse_id = _statement_client()
    print("Databricks source probe")
    print(f"warehouse_id={warehouse_id}")

    for table in SOURCE_TABLES:
        print(f"\nTABLE {table}")
        try:
            columns = _run_sql(client, warehouse_id, f"DESCRIBE TABLE {_quote_fqn(table)}", row_limit=200)
            visible_columns = [row for row in columns if row and row[0] and not str(row[0]).startswith("#")]
            print(f"accessible=true column_count={len(visible_columns)}")
            print("first_columns=" + ", ".join(str(row[0]) for row in visible_columns[:20]))
            row_count = _run_sql(client, warehouse_id, f"SELECT COUNT(*) FROM {_quote_fqn(table)}", row_limit=1)
            print("row_count=" + (str(row_count[0][0]) if row_count and row_count[0] else "unknown"))
        except Exception as exc:  # noqa: BLE001
            print(f"accessible=false error={type(exc).__name__}: {str(exc)[:240]}")


if __name__ == "__main__":
    main()
