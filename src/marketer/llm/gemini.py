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
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.4,
        max_output_tokens: int = 8192,
    ) -> tuple[PostEnrichment | None, str, Exception | None]:
        """Run a single structured-output call.

        Returns (parsed_model_or_None, raw_text, error_or_None).
        If parsing fails, parsed is None but raw_text carries what the model returned.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=PostEnrichment,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=config,
            )
        except Exception as exc:
            logger.exception("Gemini call failed")
            return None, "", exc

        raw_text = getattr(response, "text", "") or ""

        # Preferred path: google-genai parses into the Pydantic model when response_schema is set
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, PostEnrichment):
            return parsed, raw_text, None

        # Fallback: parse the raw text ourselves
        try:
            return PostEnrichment.model_validate_json(raw_text), raw_text, None
        except Exception as exc:
            return None, raw_text, exc

    def repair(
        self,
        system_prompt: str,
        repair_prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 8192,
    ) -> tuple[PostEnrichment | None, str, Exception | None]:
        """Schema-repair round-trip. Same shape as generate_structured."""
        return self.generate_structured(
            system_prompt=system_prompt,
            user_prompt=repair_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )


def serialize_for_prompt(ctx_json: dict, truncate_lists: int) -> str:
    """Render the prompt context as compact JSON, truncating long list fields."""
    truncated = _truncate_lists(ctx_json, truncate_lists)
    return json.dumps(truncated, ensure_ascii=False, indent=2)


def _truncate_lists(obj, cap: int):
    if isinstance(obj, list):
        return [_truncate_lists(x, cap) for x in obj[:cap]]
    if isinstance(obj, dict):
        return {k: _truncate_lists(v, cap) for k, v in obj.items()}
    return obj
