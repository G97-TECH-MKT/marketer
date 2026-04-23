"""Thin wrapper around google-genai for structured output.

MVP: single-shot call + optional repair attempt. No streaming, no tools.
"""

from __future__ import annotations

import json
import logging

from google import genai
from google.genai import types

from marketer.schemas.enrichment import PostEnrichment

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 30):
        timeout_ms = max(1, int(timeout_seconds)) * 1000
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        self._model = model
        self._timeout_ms = timeout_ms

    @property
    def model_name(self) -> str:
        return self._model

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.4,
        max_output_tokens: int = 8192,
    ) -> tuple[PostEnrichment | None, str, Exception | None, dict]:
        """Run a single structured-output call.

        Returns (parsed_model_or_None, raw_text, error_or_None, usage_dict).
        usage_dict keys: input_tokens, output_tokens, thoughts_tokens.
        If parsing fails, parsed is None but raw_text carries what the model returned.

        Uses JSON mode (response_mime_type only, no response_schema) — constrained
        generation with complex schemas causes Gemini to truncate output early.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=config,
            )
        except Exception as exc:
            logger.exception("Gemini call failed")
            return None, "", exc, {}

        raw_text = getattr(response, "text", "") or ""
        um = getattr(response, "usage_metadata", None)
        usage: dict = {
            "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
            "thoughts_tokens": getattr(um, "thoughts_token_count", 0) or 0,
        }

        # Log finish reason so truncation/safety stops are diagnosable.
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            if finish_reason and str(finish_reason) not in (
                "FinishReason.STOP",
                "STOP",
                "1",
            ):
                logger.warning(
                    "Gemini finish_reason=%s output_tokens=%d",
                    finish_reason,
                    usage["output_tokens"],
                )

        try:
            return PostEnrichment.model_validate_json(raw_text), raw_text, None, usage
        except Exception as exc:
            logger.warning(
                "PostEnrichment parse failed len=%d preview=%r",
                len(raw_text),
                raw_text[:200],
            )
            return None, raw_text, exc, usage

    def repair(
        self,
        system_prompt: str,
        repair_prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 8192,
    ) -> tuple[PostEnrichment | None, str, Exception | None, dict]:
        """Schema-repair round-trip. Same shape as generate_structured."""
        return self.generate_structured(
            system_prompt=system_prompt,
            user_prompt=repair_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )


def is_timeout_exception(exc: Exception | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "deadline" in name:
        return True
    timeout_markers: tuple[str, ...] = (
        "deadline_exceeded",
        "deadline exceeded",
        "deadline expired",
        "timed out",
        "timeout",
        "504",
    )
    return any(marker in message for marker in timeout_markers)


def serialize_for_prompt(
    ctx_json: dict, truncate_lists: int, truncate_text: int = 600
) -> str:
    """Render prompt context JSON with bounded list and text sizes."""
    truncated = _truncate_lists_and_text(ctx_json, truncate_lists, truncate_text)
    return json.dumps(truncated, ensure_ascii=False, indent=2)


def _truncate_lists_and_text(obj, list_cap: int, text_cap: int):
    if isinstance(obj, list):
        return [_truncate_lists_and_text(x, list_cap, text_cap) for x in obj[:list_cap]]
    if isinstance(obj, dict):
        return {
            k: _truncate_lists_and_text(v, list_cap, text_cap) for k, v in obj.items()
        }
    if isinstance(obj, str) and text_cap > 0 and len(obj) > text_cap:
        return f"{obj[:text_cap]}… [truncated {len(obj) - text_cap} chars]"
    return obj
