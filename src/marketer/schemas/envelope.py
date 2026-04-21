"""Lenient ROUTER envelope model.

Uses `extra='allow'` throughout so any field ROUTER adds flows through without
breaking MARKETER. Minimum required fields are validated explicitly in the API
layer (SPEC §4.2).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RouterEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    action_code: str
    callback_url: str
    payload: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = None
    action_id: str | None = None
    correlation_id: str | None = None
