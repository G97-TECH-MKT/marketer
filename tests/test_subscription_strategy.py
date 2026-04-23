"""Tests for subscription_strategy action: normalizer, schema, and multi-output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketer.normalizer import normalize
from marketer.schemas.enrichment import MultiEnrichmentOutput, PostEnrichment
from marketer.schemas.internal_context import SubscriptionJob

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENVELOPES = ROOT / "tests" / "fixtures" / "envelopes"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ENVELOPES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Normalizer: subscription_jobs extraction + quantity expansion
# ---------------------------------------------------------------------------


class TestNormalizerSubscriptionStrategy:
    def test_parses_and_expands_by_quantity(self):
        """Fixture has job[0] quantity=2 + job[1] quantity=1 → 3 expanded jobs."""
        data = _load("subscription_strategy.json")
        ctx, warnings = normalize(data)
        assert ctx.action_code == "subscription_strategy"
        assert ctx.subscription_jobs is not None
        # 2 (from quantity=2) + 1 (from quantity=1) = 3
        assert len(ctx.subscription_jobs) == 3
        assert ctx.subscription_jobs[0].index == 0
        assert ctx.subscription_jobs[1].index == 1
        assert ctx.subscription_jobs[2].index == 2
        # First two share action_key from the same original job
        assert ctx.subscription_jobs[0].action_key == "create_post"
        assert ctx.subscription_jobs[1].action_key == "create_post"
        assert ctx.subscription_jobs[0].description == ctx.subscription_jobs[1].description

    def test_router_fields_captured(self):
        data = _load("subscription_strategy.json")
        ctx, _ = normalize(data)
        job = ctx.subscription_jobs[0]
        assert job.slug == "POST-INSTAGRAM"
        assert job.orchestrator_agent == "job-router"
        assert job.product_uuid == "prod-uuid-001"
        assert job.quantity == 2

    def test_top_level_description_used_as_user_request(self):
        data = _load("subscription_strategy.json")
        ctx, _ = normalize(data)
        assert "estrategia de contenido" in ctx.user_request.lower()

    def test_brief_still_extracted(self):
        data = _load("subscription_strategy.json")
        ctx, _ = normalize(data)
        assert ctx.brief is not None
        assert ctx.brief.business_name is not None

    def test_missing_jobs_raises(self):
        data = _load("subscription_strategy.json")
        del data["payload"]["client_request"]["jobs"]
        with pytest.raises(ValueError, match="subscription_strategy requires"):
            normalize(data)

    def test_empty_jobs_raises(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = []
        with pytest.raises(ValueError, match="subscription_strategy requires"):
            normalize(data)

    def test_invalid_job_items_skipped_with_warning(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Valid job"},
            {"action_key": "create_post"},  # missing description → skipped
            "not_a_dict",
        ]
        ctx, warnings = normalize(data)
        assert ctx.subscription_jobs is not None
        assert len(ctx.subscription_jobs) == 1
        codes = {w.code for w in warnings}
        assert "job_missing_description" in codes
        assert "job_invalid" in codes

    def test_empty_action_key_defaults_to_create_prod_line(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "", "description": "Fallback job"},
        ]
        ctx, _ = normalize(data)
        assert ctx.subscription_jobs is not None
        assert len(ctx.subscription_jobs) == 1
        assert ctx.subscription_jobs[0].action_key == "create_prod_line"

    def test_missing_action_key_defaults_to_create_prod_line(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"description": "No action_key field at all"},
        ]
        ctx, _ = normalize(data)
        assert ctx.subscription_jobs is not None
        assert len(ctx.subscription_jobs) == 1
        assert ctx.subscription_jobs[0].action_key == "create_prod_line"

    def test_all_jobs_missing_description_raises(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post"},
            {"action_key": ""},
        ]
        with pytest.raises(ValueError, match="subscription_strategy requires"):
            normalize(data)


class TestQuantityExpansion:
    def test_quantity_1_produces_1_job(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Single", "quantity": 1}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 1

    def test_quantity_3_produces_3_jobs(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Triple", "quantity": 3}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 3
        assert all(j.action_key == "create_post" for j in ctx.subscription_jobs)
        assert [j.index for j in ctx.subscription_jobs] == [0, 1, 2]

    def test_quantity_0_treated_as_1(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Zero qty", "quantity": 0}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 1

    def test_quantity_negative_treated_as_1(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Neg", "quantity": -5}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 1

    def test_quantity_capped_at_max(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "Huge", "quantity": 100}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 10  # MAX_QUANTITY_PER_JOB

    def test_quantity_missing_defaults_to_1(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "No qty field"}
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 1

    def test_multiple_jobs_expand_independently(self):
        data = _load("subscription_strategy.json")
        data["payload"]["client_request"]["jobs"] = [
            {"action_key": "create_post", "description": "A", "quantity": 2},
            {"action_key": "create_post", "description": "B", "quantity": 3},
        ]
        ctx, _ = normalize(data)
        assert len(ctx.subscription_jobs) == 5
        assert [j.index for j in ctx.subscription_jobs] == [0, 1, 2, 3, 4]
        assert [j.description for j in ctx.subscription_jobs] == ["A", "A", "B", "B", "B"]


# ---------------------------------------------------------------------------
# Schema: MultiEnrichmentOutput
# ---------------------------------------------------------------------------


class TestMultiEnrichmentOutput:
    def _make_enrichment_dict(self, title: str = "Test") -> dict:
        """Minimal PostEnrichment dict for testing."""
        return {
            "schema_version": "2.0",
            "surface_format": "post",
            "content_pillar": "product",
            "title": title,
            "objective": "Test objective",
            "brand_dna": "Test DNA",
            "strategic_decisions": {
                "surface_format": {
                    "chosen": "post",
                    "alternatives_considered": ["story"],
                    "rationale": "Test",
                },
                "angle": {
                    "chosen": "product angle",
                    "alternatives_considered": ["education"],
                    "rationale": "Test",
                },
                "voice": {
                    "chosen": "warm voice",
                    "alternatives_considered": ["formal"],
                    "rationale": "Test",
                },
            },
            "visual_style_notes": "Test notes",
            "image": {
                "concept": "Test concept",
                "generation_prompt": "Test prompt",
                "alt_text": "Test alt",
            },
            "caption": {"hook": "Hook", "body": "Body", "cta_line": "CTA"},
            "cta": {"channel": "none", "label": ""},
            "hashtag_strategy": {
                "intent": "brand_awareness",
                "suggested_volume": 5,
                "themes": ["test"],
                "tags": ["#test"],
            },
            "do_not": [],
            "brand_intelligence": {
                "business_taxonomy": "test_business",
                "funnel_stage_target": "awareness",
                "voice_register": "test voice",
                "emotional_beat": "test",
                "audience_persona": "Test persona",
                "unfair_advantage": "Test advantage",
                "risk_flags": [],
                "rhetorical_device": "contraste",
            },
            "cf_post_brief": "Test brief",
        }

    def test_parse_valid_multi_output(self):
        data = {"items": [self._make_enrichment_dict("Post 1"), self._make_enrichment_dict("Post 2")]}
        result = MultiEnrichmentOutput.model_validate(data)
        assert len(result.items) == 2
        assert result.items[0].title == "Post 1"
        assert result.items[1].title == "Post 2"

    def test_parse_from_json(self):
        data = {"items": [self._make_enrichment_dict()]}
        raw = json.dumps(data)
        result = MultiEnrichmentOutput.model_validate_json(raw)
        assert len(result.items) == 1

    def test_empty_items_valid(self):
        result = MultiEnrichmentOutput.model_validate({"items": []})
        assert len(result.items) == 0

    def test_missing_items_raises(self):
        with pytest.raises(Exception):
            MultiEnrichmentOutput.model_validate({})


# ---------------------------------------------------------------------------
# SubscriptionJob model
# ---------------------------------------------------------------------------


class TestSubscriptionJob:
    def test_create_with_defaults(self):
        job = SubscriptionJob(action_key="create_post", description="Test", index=0)
        assert job.action_key == "create_post"
        assert job.index == 0
        assert job.quantity == 1
        assert job.slug is None
        assert job.orchestrator_agent is None
        assert job.product_uuid is None

    def test_create_with_router_fields(self):
        job = SubscriptionJob(
            action_key="create_post",
            description="Test",
            index=0,
            quantity=2,
            slug="POST-INSTAGRAM",
            orchestrator_agent="job-router",
            product_uuid="prod-001",
        )
        assert job.quantity == 2
        assert job.slug == "POST-INSTAGRAM"
        assert job.orchestrator_agent == "job-router"
        assert job.product_uuid == "prod-001"
