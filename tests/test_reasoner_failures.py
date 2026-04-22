from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marketer.reasoner import reason


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "envelopes" / "minimal_post.json"
)


class _TimeoutGemini:
    def __init__(self) -> None:
        self.model_name = "fake-model"
        self.repair_called = False

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 16384,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, user_prompt, max_output_tokens
        return (
            None,
            "",
            RuntimeError(
                "504 DEADLINE_EXCEEDED. Deadline expired before operation could complete."
            ),
            {},
        )

    def repair(
        self,
        system_prompt: str,
        repair_prompt: str,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, repair_prompt
        self.repair_called = True
        return None, "", RuntimeError("should not be called"), {}


class _SchemaFailGemini:
    def __init__(self) -> None:
        self.model_name = "fake-model"
        self.repair_called = False

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 16384,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, user_prompt, max_output_tokens
        return None, "not-json", ValueError("invalid json"), {}

    def repair(
        self,
        system_prompt: str,
        repair_prompt: str,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, repair_prompt
        self.repair_called = True
        return None, "", ValueError("still invalid"), {}


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_reason_returns_llm_timeout_without_repair() -> None:
    gemini = _TimeoutGemini()
    callback = reason(_load_fixture(), gemini=gemini)

    assert callback.status == "FAILED"
    assert callback.error_message is not None
    assert callback.error_message.startswith("llm_timeout:")
    assert "DEADLINE_EXCEEDED" in callback.error_message
    assert gemini.repair_called is False


def test_reason_keeps_schema_validation_failed_for_non_timeout_errors() -> None:
    gemini = _SchemaFailGemini()
    callback = reason(_load_fixture(), gemini=gemini)

    assert callback.status == "FAILED"
    assert callback.error_message is not None
    assert callback.error_message.startswith("schema_validation_failed:")
    assert gemini.repair_called is True
