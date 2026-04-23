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
        max_output_tokens: int = 16384,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, repair_prompt, max_output_tokens
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
        max_output_tokens: int = 16384,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, repair_prompt, max_output_tokens
        self.repair_called = True
        return None, "", ValueError("still invalid"), {}


class _TruncatedThenRecoverGemini:
    def __init__(self) -> None:
        self.model_name = "fake-model"
        self.repair_calls = 0

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 16384,
    ) -> tuple[None, str, Exception, dict[str, int]]:
        del system_prompt, user_prompt, max_output_tokens
        truncated = '{\n  "surface_format": "post",\n  "title": "Broken'
        return (
            None,
            truncated,
            ValueError(
                "1 validation error for PostEnrichment Invalid JSON: EOF while parsing a string at line 3 column 20 [type=json_invalid]"
            ),
            {"input_tokens": 10, "output_tokens": 10, "thoughts_tokens": 0},
        )

    def repair(
        self,
        system_prompt: str,
        repair_prompt: str,
        max_output_tokens: int = 16384,
    ) -> tuple[Any, str, Exception | None, dict[str, int]]:
        del system_prompt, repair_prompt, max_output_tokens
        self.repair_calls += 1
        if self.repair_calls == 1:
            return (
                None,
                '{"title":"still-broken',
                ValueError(
                    "1 validation error for PostEnrichment Invalid JSON: EOF while parsing a string at line 1 column 22 [type=json_invalid]"
                ),
                {"input_tokens": 5, "output_tokens": 5, "thoughts_tokens": 0},
            )
        payload = {
            "schema_version": "2.0",
            "surface_format": "post",
            "content_pillar": "product",
            "title": "OK",
            "objective": "Objetivo",
            "brand_dna": "CLIENT DNA — Test",
            "strategic_decisions": {
                "surface_format": {
                    "chosen": "post",
                    "alternatives_considered": ["story"],
                    "rationale": "brief",
                },
                "angle": {
                    "chosen": "angle concreto",
                    "alternatives_considered": ["otro"],
                    "rationale": "brief",
                },
                "voice": {
                    "chosen": "voz cercana",
                    "alternatives_considered": ["formal"],
                    "rationale": "brief",
                },
            },
            "visual_style_notes": "Simple",
            "narrative_connection": None,
            "image": {
                "concept": "concept",
                "generation_prompt": "prompt",
                "alt_text": "alt",
            },
            "caption": {"hook": "hook", "body": "body", "cta_line": ""},
            "cta": {"channel": "dm", "url_or_handle": None, "label": "DM"},
            "hashtag_strategy": {
                "intent": "brand_awareness",
                "suggested_volume": 5,
                "themes": [],
                "tags": ["#uno", "#dos", "#tres", "#cuatro", "#cinco"],
            },
            "do_not": [],
            "selected_images": [],
            "visual_selection": {
                "recommended_asset_urls": [],
                "recommended_reference_urls": [],
                "avoid_asset_urls": [],
            },
            "confidence": {
                "surface_format": "medium",
                "angle": "medium",
                "palette_match": "medium",
                "cta_channel": "medium",
            },
            "brand_intelligence": {
                "business_taxonomy": "local_food_service",
                "funnel_stage_target": "awareness",
                "voice_register": "cercana y directa",
                "emotional_beat": "confianza",
                "audience_persona": "Persona local con interés en calidad.",
                "unfair_advantage": "Dato diferencial del brief.",
                "risk_flags": [],
                "rhetorical_device": "ninguno",
            },
            "cf_post_brief": "CONCEPT — sujeto\nCaption:\nhook\nbody\nHashtags:\n#uno #dos #tres #cuatro #cinco",
        }
        from marketer.schemas.enrichment import PostEnrichment

        model = PostEnrichment.model_validate(payload)
        return model, json.dumps(payload), None, {
            "input_tokens": 8,
            "output_tokens": 8,
            "thoughts_tokens": 0,
        }


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


def test_reason_recovers_from_truncated_json_with_compact_repair_retry() -> None:
    gemini = _TruncatedThenRecoverGemini()
    callback = reason(_load_fixture(), gemini=gemini)

    assert callback.status == "COMPLETED"
    assert callback.output_data is not None
    assert gemini.repair_calls == 2
