"""Env-based settings for MARKETER."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    llm_timeout_seconds: int = 30
    log_level: str = "INFO"
    extras_list_truncation: int = 10

    # Router integration
    orch_callback_api_key: str = ""
    inbound_token: str = ""
    callback_http_timeout_seconds: float = 30.0
    callback_retry_attempts: int = 2


def load_settings() -> Settings:
    return Settings()
