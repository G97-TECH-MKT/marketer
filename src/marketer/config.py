"""Env-based settings for MARKETER."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    llm_timeout_seconds: int = 30
    llm_max_output_tokens: int = 16384
    prompt_text_truncation_chars: int = 600
    log_level: str = "INFO"
    extras_list_truncation: int = 10

    # Fan-out for subscription_strategy multi-job batches.
    # When enabled, reason_multi splits the batch into 1 brand_dna call + N
    # parallel single-job calls (bounded by Semaphore) instead of 1 giant call,
    # which exceeds Gemini's server-side ~180s deadline for large batches.
    llm_fanout_enabled: bool = True
    llm_fanout_concurrency: int = 5
    llm_brand_dna_max_tokens: int = 2048

    # Router integration
    orch_callback_api_key: str = ""
    inbound_token: str = ""
    callback_http_timeout_seconds: float = 30.0
    callback_retry_attempts: int = 2

    # Persistence (Postgres). Empty → degraded mode (no persistence).
    database_url: str = ""
    db_pool_size: int = 10
    db_pool_max_overflow: int = 5
    db_pool_timeout_seconds: float = 10.0
    # Tests set this to avoid cross-loop connection reuse with TestClient;
    # prod leaves it False to keep the real pool.
    db_use_null_pool: bool = False

    # USP Memory Gateway. Empty key or URL → fetch skipped entirely.
    usp_graphql_url: str = ""
    usp_api_key: str = ""
    usp_timeout_seconds: float = 5.0

    # Agentic Task Dispatcher. Empty → dispatcher disabled (prod-line jobs skip POST).
    agentic_dispatcher_url: str = ""

    # Gallery Image Pool. Both URL and key must be non-empty to activate.
    gallery_api_url: str = ""
    gallery_api_key: str = ""
    gallery_timeout_seconds: float = 5.0
    gallery_page_size: int = 50
    gallery_vision_candidates: int = 5


def load_settings() -> Settings:
    return Settings()
