from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    control_plane_database_url: str = "sqlite:///./data/control_plane.db"
    databricks_host: str | None = None
    databricks_auth_type: str | None = None
    databricks_token: str | None = None
    databricks_client_id: str | None = None
    databricks_client_secret: str | None = None
    databricks_sql_warehouse_id: str | None = None
    databricks_sql_wait_timeout: str = "30s"
    databricks_sql_poll_timeout_seconds: int = 120
    databricks_resolver_mode: str = "auto"
    databricks_genie_space_id: str | None = None
    databricks_genie_poll_timeout_seconds: int = 120
    databricks_genie_poll_interval_seconds: int = 2
    databricks_genie_request_timeout_seconds: int = 60
    databricks_genie_batch_size: int = 50
    databricks_genie_max_prompt_chars: int = 23000
    databricks_genie_options_per_candidate: int = 5
    databricks_sql_mapping_model: str = "databricks-claude-sonnet-4-6"
    databricks_sql_mapping_timeout_seconds: int = 45

    skyvern_base_url: str = "http://localhost:8000"
    skyvern_api_key: str | None = None
    skyvern_webhook_secret: str = ""
    control_plane_public_base_url: str = "http://localhost:8010"
    temporal_target_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_signal_enabled: bool = False
    skyvern_max_fields_per_task: int = 8
    skyvern_task_complexity_budget_chars: int = 1400
    skyvern_validate_max_steps: int = 25
    skyvern_scan_max_steps: int = 35
    skyvern_fill_max_steps: int = 45

    openai_api_base: str | None = None
    openai_api_key: str | None = None
    openai_default_model: str | None = None
    pdf_embedding_mapping_enabled: bool = False
    pdf_embedding_model: str = "text-embedding-3-small"
    pdf_embedding_api_base: str | None = None
    pdf_embedding_api_key: str | None = None
    pdf_embedding_timeout_seconds: int = 20
    pdf_openai_label_enrichment_enabled: bool = False
    pdf_openai_label_enrichment_model: str = "gpt-4o-mini"
    pdf_openai_label_enrichment_api_base: str = "https://api.openai.com/v1"
    pdf_openai_label_enrichment_api_key: str | None = None
    pdf_openai_label_enrichment_batch_size: int = 25
    pdf_openai_label_enrichment_timeout_seconds: int = 300
    pdf_openai_pages_per_batch: int = 5
    pdf_openai_max_concurrent_batches: int = 5
    pdf_openai_max_fields_per_batch: int = 30
    pdf_label_enrichment_provider: str = "openai"
    pdf_label_enrichment_candidate_limit: int = 250
    pdf_upload_dir: str = "/app/uploads"
    pdf_export_dir: str = "/app/data/pdf_exports"
    pdf_databricks_label_model: str | None = None
    pdf_databricks_label_batch_size: int = 100
    pdf_databricks_label_max_prompt_chars: int = 18000
    pdf_databricks_label_timeout_seconds: int = 45

    browser_type: str = "chromium-headful"
    browser_remote_debugging_url: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
