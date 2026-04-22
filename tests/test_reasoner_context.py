"""Tests for _build_prompt_context and resources assembly in reasoner.

All tests are offline — no LLM, no DB, no network.
Covers:
- gallery_pool serialization into prompt context
- user_attachments serialization into prompt context
- resources assembly priority order (attachments > gallery_picks > legacy)
- deduplication in resources
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from marketer.reasoner import _build_prompt_context, reason
from marketer.schemas.enrichment import (
    BrandIntelligence,
    CallToAction,
    CaptionParts,
    Confidence,
    HashtagStrategy,
    ImageBrief,
    PostEnrichment,
    SelectedImage,
    StrategicChoice,
    StrategicDecisions,
    VisualSelection,
)
from marketer.schemas.internal_context import (
    BrandTokens,
    GalleryPool,
    GalleryPoolItem,
    InternalContext,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENVELOPES = ROOT / "tests" / "fixtures" / "envelopes"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ENVELOPES / name).read_text(encoding="utf-8"))


def _minimal_ctx(**kwargs: Any) -> InternalContext:
    defaults: dict[str, Any] = dict(
        task_id="t1",
        callback_url="https://cb.example/t1",
        action_code="create_post",
        surface="post",
        mode="create",
        user_request="Crea un post sobre bienestar",
    )
    defaults.update(kwargs)
    return InternalContext(**defaults)


def _pool_item(
    uuid: str = "img-001",
    url: str = "https://s3.example.com/img-001.png",
    score: float = 9.0,
) -> GalleryPoolItem:
    return GalleryPoolItem(
        uuid=uuid,
        content_url=url,
        category="Inspiración",
        description="Bruno en namaste",
        metadata={"tags": ["founder", "calm"]},
        score=score,
    )


def _base_enrichment(**overrides: Any) -> PostEnrichment:
    defaults: dict[str, Any] = dict(
        surface_format="post",
        content_pillar="product",
        title="Test",
        objective="Test objective.",
        brand_dna="CLIENT DNA — Test",
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(chosen="post", alternatives_considered=[], rationale="r"),
            angle=StrategicChoice(chosen="bienestar", alternatives_considered=[], rationale="r"),
            voice=StrategicChoice(chosen="cercano", alternatives_considered=[], rationale="r"),
        ),
        visual_style_notes="Clean.",
        image=ImageBrief(concept="Hero.", generation_prompt="Photo.", alt_text="Alt."),
        caption=CaptionParts(hook="Hook.", body="Body.", cta_line=""),
        cta=CallToAction(channel="dm", label="DM"),
        hashtag_strategy=HashtagStrategy(
            intent="brand_awareness", suggested_volume=5, themes=["wellness"], tags=["#relax"]
        ),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="local_pro_service",
            funnel_stage_target="awareness",
            voice_register="íntimo-elegante",
            emotional_beat="tranquilidad",
            audience_persona="Hombres 35-50 que buscan bienestar.",
            unfair_advantage="Fundador presente en cada sesión.",
            risk_flags=[],
            rhetorical_device="contraste",
        ),
    )
    defaults.update(overrides)
    return PostEnrichment(**defaults)


class _FakeGemini:
    """Fake GeminiClient — returns a fixed enrichment without any network call."""

    model_name = "fake-gemini"

    def __init__(self, enrichment: PostEnrichment):
        self._enrichment = enrichment

    def generate_structured(self, **_kwargs: Any):
        return (
            self._enrichment,
            "",
            None,
            {"input_tokens": 10, "output_tokens": 10, "thoughts_tokens": 0},
        )

    def repair(self, **_kwargs: Any):
        return (
            self._enrichment,
            "",
            None,
            {"input_tokens": 5, "output_tokens": 5, "thoughts_tokens": 0},
        )


# ===========================================================================
# _build_prompt_context — gallery_pool serialization
# ===========================================================================


class TestBuildPromptContextGalleryPool:

    def test_gallery_pool_key_present_when_pool_set(self):
        pool = GalleryPool(shortlist=[_pool_item()], total_fetched=5, total_eligible=1)
        ctx = _minimal_ctx(gallery_pool=pool)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["gallery_pool"] is not None
        assert len(parsed["gallery_pool"]) == 1

    def test_gallery_pool_item_fields_serialized(self):
        item = _pool_item(uuid="img-abc", url="https://s3.example.com/img-abc.png", score=7.5)
        pool = GalleryPool(shortlist=[item])
        ctx = _minimal_ctx(gallery_pool=pool)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)

        entry = parsed["gallery_pool"][0]
        assert entry["uuid"] == "img-abc"
        assert entry["content_url"] == "https://s3.example.com/img-abc.png"
        assert entry["score"] == 7.5
        assert entry["category"] == "Inspiración"
        assert entry["description"] == "Bruno en namaste"

    def test_gallery_pool_null_when_no_pool(self):
        ctx = _minimal_ctx(gallery_pool=None)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["gallery_pool"] is None

    def test_gallery_pool_null_when_empty_shortlist(self):
        pool = GalleryPool(shortlist=[], total_fetched=0, total_eligible=0)
        ctx = _minimal_ctx(gallery_pool=pool)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["gallery_pool"] is None

    def test_multiple_pool_items_all_serialized(self):
        items = [_pool_item(f"img-{i:02d}", f"https://s3.example.com/img-{i:02d}.png") for i in range(3)]
        pool = GalleryPool(shortlist=items)
        ctx = _minimal_ctx(gallery_pool=pool)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert len(parsed["gallery_pool"]) == 3
        uuids = {e["uuid"] for e in parsed["gallery_pool"]}
        assert uuids == {"img-00", "img-01", "img-02"}


# ===========================================================================
# _build_prompt_context — user_attachments serialization
# ===========================================================================


class TestBuildPromptContextAttachments:

    def test_user_attachments_present_when_set(self):
        ctx = _minimal_ctx(attachments=["https://s3.example.com/user-img.jpg"])
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["user_attachments"] == ["https://s3.example.com/user-img.jpg"]

    def test_user_attachments_null_when_empty(self):
        ctx = _minimal_ctx(attachments=[])
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["user_attachments"] is None

    def test_user_attachments_multiple_urls(self):
        urls = ["https://s3.example.com/a.jpg", "https://s3.example.com/b.jpg"]
        ctx = _minimal_ctx(attachments=urls)
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["user_attachments"] == urls

    def test_both_gallery_pool_and_attachments_present_together(self):
        pool = GalleryPool(shortlist=[_pool_item()])
        ctx = _minimal_ctx(
            gallery_pool=pool,
            attachments=["https://s3.example.com/user-img.jpg"],
        )
        output = _build_prompt_context(ctx, extras_truncation=10)
        parsed = json.loads(output)
        assert parsed["gallery_pool"] is not None
        assert parsed["user_attachments"] is not None


# ===========================================================================
# Resources assembly — priority order and deduplication
# ===========================================================================


class TestResourcesAssembly:
    """Tests for the resources merging in reason() using a fake GeminiClient."""

    def _call_reason(
        self,
        enrichment: PostEnrichment,
        *,
        attachments: list[str] | None = None,
        gallery_pool: GalleryPool | None = None,
    ):
        envelope = _load("nubiex_golden_input.json")
        if attachments is not None:
            envelope.setdefault("payload", {}).setdefault("client_request", {})
            envelope["payload"]["client_request"]["attachments"] = attachments
        gemini = _FakeGemini(enrichment)
        return reason(
            envelope,
            gemini,
            extras_truncation=10,
            gallery_pool=gallery_pool,
        )

    def test_user_attachments_always_in_resources(self):
        # Attachments bypass the LLM and always land in resources
        attachment_url = "https://s3.example.com/user-sent.jpg"
        enrichment = _base_enrichment(
            selected_images=[],
            visual_selection=VisualSelection(recommended_asset_urls=[]),
        )
        cb = self._call_reason(enrichment, attachments=[attachment_url])
        assert cb.status == "COMPLETED"
        assert attachment_url in cb.output_data.data.resources

    def test_selected_images_content_urls_in_resources(self):
        # selected_images from gallery_pool picks appear in resources
        gallery_url = "https://s3.example.com/gallery-pick.jpg"
        pool = GalleryPool(shortlist=[_pool_item(url=gallery_url)])
        enrichment = _base_enrichment(
            selected_images=[
                SelectedImage(
                    uuid="img-001",
                    content_url=gallery_url,
                    role="hero",
                    usage_note="Best match for brief.",
                )
            ],
            visual_selection=VisualSelection(recommended_asset_urls=[]),
        )
        cb = self._call_reason(enrichment, gallery_pool=pool)
        assert cb.status == "COMPLETED"
        assert gallery_url in cb.output_data.data.resources

    def test_attachments_come_before_gallery_picks(self):
        attachment_url = "https://s3.example.com/user-sent.jpg"
        gallery_url = "https://s3.example.com/gallery-pick.jpg"
        pool = GalleryPool(shortlist=[_pool_item(url=gallery_url)])
        enrichment = _base_enrichment(
            selected_images=[
                SelectedImage(uuid="img-001", content_url=gallery_url, role="hero", usage_note="fit")
            ],
            visual_selection=VisualSelection(recommended_asset_urls=[]),
        )
        cb = self._call_reason(enrichment, attachments=[attachment_url], gallery_pool=pool)
        assert cb.status == "COMPLETED"
        resources = cb.output_data.data.resources
        assert resources.index(attachment_url) < resources.index(gallery_url)

    def test_duplicate_urls_deduplicated(self):
        # Same URL in both attachments and selected_images → appears once
        shared_url = "https://s3.example.com/shared.jpg"
        pool = GalleryPool(shortlist=[_pool_item(url=shared_url)])
        enrichment = _base_enrichment(
            selected_images=[
                SelectedImage(uuid="img-001", content_url=shared_url, role="hero", usage_note="fit")
            ],
            visual_selection=VisualSelection(recommended_asset_urls=[]),
        )
        cb = self._call_reason(enrichment, attachments=[shared_url], gallery_pool=pool)
        assert cb.status == "COMPLETED"
        resources = cb.output_data.data.resources
        assert resources.count(shared_url) == 1

    def test_no_images_produces_empty_resources(self):
        enrichment = _base_enrichment(
            selected_images=[],
            visual_selection=VisualSelection(recommended_asset_urls=[]),
        )
        cb = self._call_reason(enrichment)
        assert cb.status == "COMPLETED"
        assert cb.output_data.data.resources == []
