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
    log_level: str = "INFO"
    extras_list_truncation: int = 10

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


def load_settings() -> Settings:
    return Settings()
