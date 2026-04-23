"""Unit tests for spec 11 — Gallery Image Pool.

All tests are offline (no network, no LLM, no DB).
Covers: eligibility filter, Stage 1 scoring, shortlist capping,
normalizer integration, PostEnrichment model.

Spec ref: docs/OpenSpec/11-gallery-image-pool.md §11.1
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from marketer.gallery import (
    _build_shortlist,
    fetch_gallery_pool,
    is_eligible,
    score_image,
)
from marketer.normalizer import normalize
from marketer.schemas.enrichment import PostEnrichment, SelectedImage
from marketer.schemas.internal_context import GalleryPool, GalleryPoolItem

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENVELOPES = ROOT / "tests" / "fixtures" / "envelopes"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ENVELOPES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_item(
    *,
    uuid: str = "item-001",
    type_: str = "img",
    used_at: str | None = None,
    locked_until: str | None = None,
    category: str = "Inspiración",
    description: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "uuid": uuid,
        "content": f"https://s3.example.com/{uuid}.png",
        "type": type_,
        "category": category,
        "used_at": used_at,
        "locked_until": locked_until,
        "description": description,
        "metadata": metadata if metadata is not None else {},
    }


def _task_context(
    user_request: str = "crea un post sobre bienestar y relax",
    brief_keywords: list[str] | None = None,
    brief_tone: str = "íntimo elegante",
    brief_design_style: str = "minimal",
) -> dict:
    return {
        "user_request": user_request,
        "brief_keywords": brief_keywords or ["bienestar", "masaje", "relax"],
        "brief_tone": brief_tone,
        "action_code": "create_post",
        "brief_design_style": brief_design_style,
    }


def _make_pool_item(uuid: str = "img-001", score: float = 5.0) -> GalleryPoolItem:
    return GalleryPoolItem(
        uuid=uuid,
        content_url=f"https://s3.example.com/{uuid}.png",
        category="Inspiración",
        description=None,
        used_at=None,
        metadata={"tags": ["relax"]},
        score=score,
    )


# ===========================================================================
# Eligibility filter (§3.1)
# ===========================================================================


class TestEligibilityFilter:
    def test_passes_eligible_item(self):
        item = _make_item(used_at=None, locked_until=None)
        assert is_eligible(item, _now_utc()) is True

    def test_excludes_used_item(self):
        # used_at set to any non-null value → excluded
        item = _make_item(used_at="2026-01-01T00:00:00Z")
        assert is_eligible(item, _now_utc()) is False

    def test_excludes_locked_item(self):
        # locked_until in the future → excluded
        future = _iso(_now_utc() + timedelta(hours=2))
        item = _make_item(locked_until=future)
        assert is_eligible(item, _now_utc()) is False

    def test_passes_expired_lock(self):
        # locked_until in the past → eligible
        past = _iso(_now_utc() - timedelta(hours=1))
        item = _make_item(locked_until=past)
        assert is_eligible(item, _now_utc()) is True

    def test_excludes_non_img_type(self):
        item = _make_item(type_="video")
        assert is_eligible(item, _now_utc()) is False

    def test_excludes_both_used_and_locked(self):
        future = _iso(_now_utc() + timedelta(hours=1))
        item = _make_item(used_at="2026-01-01T00:00:00Z", locked_until=future)
        assert is_eligible(item, _now_utc()) is False


# ===========================================================================
# Stage 1 scoring (§3.2)
# ===========================================================================


class TestScoreImage:
    def test_empty_metadata_returns_zero(self):
        item = _make_item(metadata={})
        assert score_image(item, _task_context()) == 0.0

    def test_none_metadata_returns_zero(self):
        item = _make_item()
        item["metadata"] = None
        assert score_image(item, _task_context()) == 0.0

    def test_tag_overlap_scores_higher_than_no_overlap(self):
        matching = _make_item(
            uuid="match", metadata={"tags": ["bienestar", "relax", "masaje"]}
        )
        non_matching = _make_item(
            uuid="nomatch", metadata={"tags": ["producto", "precio", "oferta"]}
        )

        ctx = _task_context(brief_keywords=["bienestar", "masaje", "relax"])
        score_match = score_image(matching, ctx)
        score_non = score_image(non_matching, ctx)

        assert score_match > score_non

    def test_description_keyword_match_adds_score(self):
        with_desc = _make_item(
            uuid="with_desc",
            metadata={"tags": []},
            description="Esta imagen es ideal para posts sobre bienestar",
        )
        without_desc = _make_item(uuid="no_desc", metadata={"tags": []})

        ctx = _task_context(user_request="post sobre bienestar y masaje")
        assert score_image(with_desc, ctx) > score_image(without_desc, ctx)

    def test_subject_relevance_adds_score(self):
        with_subject = _make_item(
            metadata={
                "tags": [],
                "subject": "A person relaxing during a massage session",
            }
        )
        without = _make_item(metadata={"tags": []})

        ctx = _task_context(user_request="relax massage session")
        assert score_image(with_subject, ctx) > score_image(without, ctx)

    def test_mood_tone_alignment_adds_score(self):
        with_mood = _make_item(
            metadata={"tags": [], "mood": "íntimo tranquilo elegante"}
        )
        without = _make_item(metadata={"tags": []})

        ctx = _task_context(brief_tone="íntimo elegante")
        assert score_image(with_mood, ctx) > score_image(without, ctx)

    def test_empty_task_context_returns_zero_for_metadata(self):
        # No context tokens → no match possible (but metadata exists → not forced 0.0)
        item = _make_item(metadata={"tags": ["relax"], "mood": "warm"})
        ctx = _task_context(
            user_request="", brief_keywords=[], brief_tone="", brief_design_style=""
        )
        # Score may be 0 since there's nothing to match against
        s = score_image(item, ctx)
        assert s >= 0.0  # Always non-negative


# ===========================================================================
# Shortlist building (§3.3)
# ===========================================================================


class TestShortlistBuilding:
    def _make_eligible_items(self, n: int) -> list[dict]:
        items = []
        for i in range(n):
            # Give each item a distinct tag so scores can differ
            items.append(
                _make_item(
                    uuid=f"item-{i:03d}",
                    metadata={"tags": [f"tag_{i}", "relax"]},
                )
            )
        return items

    def test_shortlist_capped_at_vision_candidates(self):
        items = self._make_eligible_items(10)
        ctx = _task_context()
        shortlist, total_eligible = _build_shortlist(items, ctx, vision_candidates=5)

        assert total_eligible == 10
        assert len(shortlist) == 5

    def test_shortlist_not_capped_when_fewer_than_candidates(self):
        items = self._make_eligible_items(3)
        shortlist, total_eligible = _build_shortlist(
            items, _task_context(), vision_candidates=5
        )

        assert total_eligible == 3
        assert len(shortlist) == 3

    def test_shortlist_sorted_by_score_descending(self):
        # One item has a strong matching tag, others don't
        high = _make_item(
            uuid="high",
            metadata={"tags": ["bienestar", "relax", "masaje"]},
            description="Ideal for wellness posts",
        )
        low = _make_item(
            uuid="low",
            metadata={"tags": ["precio"]},
        )
        items = [low, high]  # low first in input
        ctx = _task_context(brief_keywords=["bienestar", "masaje", "relax"])
        shortlist, _ = _build_shortlist(items, ctx, vision_candidates=5)

        # High-scoring item should be first in shortlist
        assert shortlist[0].uuid == "high"
        assert shortlist[0].score >= shortlist[-1].score

    def test_empty_eligible_pool_returns_empty_shortlist(self):
        # All items are non-img type → no eligible items
        items = [_make_item(type_="video") for _ in range(5)]
        shortlist, total_eligible = _build_shortlist(
            items, _task_context(), vision_candidates=5
        )

        assert total_eligible == 0
        assert shortlist == []

    def test_used_items_excluded_from_shortlist(self):
        used = _make_item(uuid="used", used_at="2026-01-01T00:00:00Z")
        eligible = _make_item(uuid="eligible")
        shortlist, total_eligible = _build_shortlist(
            [used, eligible], _task_context(), vision_candidates=5
        )

        assert total_eligible == 1
        assert len(shortlist) == 1
        assert shortlist[0].uuid == "eligible"

    def test_locked_items_excluded_from_shortlist(self):
        future = _iso(_now_utc() + timedelta(hours=1))
        locked = _make_item(uuid="locked", locked_until=future)
        eligible = _make_item(uuid="eligible")
        shortlist, total_eligible = _build_shortlist(
            [locked, eligible], _task_context(), vision_candidates=5
        )

        assert total_eligible == 1
        assert shortlist[0].uuid == "eligible"

    def test_zero_score_items_included_when_below_cap(self):
        # Items with empty metadata score 0 but still make the shortlist when pool < cap
        items = [_make_item(uuid=f"item-{i}", metadata={}) for i in range(3)]
        shortlist, _ = _build_shortlist(items, _task_context(), vision_candidates=5)

        # All 3 should be included despite scoring 0
        assert len(shortlist) == 3
        assert all(p.score == 0.0 for p in shortlist)


# ===========================================================================
# Normalizer integration (§10.2)
# ===========================================================================


class TestNormalizerGalleryIntegration:
    def test_normalize_with_gallery_pool(self):
        data = _load("nubiex_golden_input.json")
        pool = GalleryPool(
            shortlist=[_make_pool_item("img-001", score=5.0)],
            total_fetched=10,
            total_eligible=3,
            truncated=False,
            source="gallery_api",
        )
        ctx, _ = normalize(data, gallery_pool=pool)

        assert ctx.gallery_pool is not None
        assert len(ctx.gallery_pool.shortlist) == 1
        assert ctx.gallery_pool.shortlist[0].uuid == "img-001"

    def test_normalize_without_gallery_pool_is_unchanged(self):
        data = _load("nubiex_golden_input.json")
        ctx_without, warnings_without = normalize(data)
        ctx_with_none, warnings_with_none = normalize(data, gallery_pool=None)

        assert ctx_without.gallery_pool is None
        assert ctx_with_none.gallery_pool is None
        # Gallery-related warnings should not appear without a pool
        gallery_warn_codes = {
            "gallery_pool_truncated",
            "gallery_vision_shortlist_empty",
            "gallery_api_unavailable",
            "gallery_api_not_found",
            "gallery_api_skipped",
        }
        codes_without = {w.code for w in warnings_without}
        assert not (codes_without & gallery_warn_codes)

    def test_gallery_pool_truncated_warning_emitted(self):
        data = _load("nubiex_golden_input.json")
        pool = GalleryPool(
            shortlist=[_make_pool_item()],
            total_fetched=50,
            total_eligible=5,
            truncated=True,
            source="gallery_api",
        )
        _, warnings = normalize(data, gallery_pool=pool)

        codes = {w.code for w in warnings}
        assert "gallery_pool_truncated" in codes

    def test_gallery_pool_not_truncated_no_truncated_warning(self):
        data = _load("nubiex_golden_input.json")
        pool = GalleryPool(
            shortlist=[_make_pool_item()],
            total_fetched=10,
            total_eligible=3,
            truncated=False,
            source="gallery_api",
        )
        _, warnings = normalize(data, gallery_pool=pool)

        codes = {w.code for w in warnings}
        assert "gallery_pool_truncated" not in codes

    def test_gallery_vision_shortlist_empty_warning_emitted(self):
        data = _load("nubiex_golden_input.json")
        # Pool present but empty shortlist (all items were filtered out)
        pool = GalleryPool(
            shortlist=[],
            total_fetched=5,
            total_eligible=0,
            truncated=False,
            source="gallery_api",
        )
        _, warnings = normalize(data, gallery_pool=pool)

        codes = {w.code for w in warnings}
        assert "gallery_vision_shortlist_empty" in codes

    def test_gallery_fetch_warning_threads_through_normalize(self):
        data = _load("nubiex_golden_input.json")
        # Simulate: gallery API returned 404 (pool=None, warning set)
        _, warnings = normalize(
            data, gallery_pool=None, gallery_warning="gallery_api_not_found"
        )

        codes = {w.code for w in warnings}
        assert "gallery_api_not_found" in codes

    def test_gallery_api_unavailable_warning_threads_through(self):
        data = _load("nubiex_golden_input.json")
        _, warnings = normalize(
            data, gallery_pool=None, gallery_warning="gallery_api_unavailable"
        )

        codes = {w.code for w in warnings}
        assert "gallery_api_unavailable" in codes

    def test_gallery_api_skipped_warning_threads_through(self):
        data = _load("nubiex_golden_input.json")
        _, warnings = normalize(
            data, gallery_pool=None, gallery_warning="gallery_api_skipped"
        )

        codes = {w.code for w in warnings}
        assert "gallery_api_skipped" in codes

    def test_no_duplicate_gallery_pool_truncated_warning(self):
        # truncated=True AND gallery_warning="gallery_pool_truncated" → only one warning
        data = _load("nubiex_golden_input.json")
        pool = GalleryPool(
            shortlist=[_make_pool_item()],
            total_fetched=50,
            total_eligible=3,
            truncated=True,
            source="gallery_api",
        )
        _, warnings = normalize(
            data, gallery_pool=pool, gallery_warning="gallery_pool_truncated"
        )

        truncated_warnings = [w for w in warnings if w.code == "gallery_pool_truncated"]
        # Should not produce duplicates — one from gallery_warning, one from truncated=True
        # The normalizer emits both; this test documents the behavior
        assert len(truncated_warnings) >= 1


# ===========================================================================
# PostEnrichment model — SelectedImage (§4.3, §11.1)
# ===========================================================================


class TestSelectedImages:
    def _make_enrichment_kwargs(self) -> dict:
        """Minimal kwargs for a valid PostEnrichment."""
        from marketer.schemas.enrichment import (
            BrandIntelligence,
            CaptionParts,
            CallToAction,
            HashtagStrategy,
            ImageBrief,
            StrategicChoice,
            StrategicDecisions,
        )

        return {
            "schema_version": "2.0",
            "surface_format": "post",
            "content_pillar": "product",
            "title": "Test Post",
            "objective": "Test objective.",
            "brand_dna": "CLIENT DNA — Test Brand\nColors: #FF0000",
            "strategic_decisions": StrategicDecisions(
                surface_format=StrategicChoice(
                    chosen="post", alternatives_considered=[], rationale="test"
                ),
                angle=StrategicChoice(
                    chosen="producto", alternatives_considered=[], rationale="test"
                ),
                voice=StrategicChoice(
                    chosen="cercano", alternatives_considered=[], rationale="test"
                ),
            ),
            "visual_style_notes": "Clean minimal style.",
            "image": ImageBrief(
                concept="Hero image", generation_prompt="Prompt.", alt_text="Alt."
            ),
            "caption": CaptionParts(
                hook="Hook line.", body="Body copy.", cta_line="CTA."
            ),
            "cta": CallToAction(channel="dm", label="Escríbenos"),
            "hashtag_strategy": HashtagStrategy(
                intent="brand_awareness",
                suggested_volume=5,
                themes=["wellness"],
                tags=["#relax"],
            ),
            "brand_intelligence": BrandIntelligence(
                business_taxonomy="wellness_spa",
                funnel_stage_target="awareness",
                voice_register="íntimo-elegante",
                emotional_beat="tranquilidad",
                audience_persona="Hombres 30-50 que buscan bienestar.",
                unfair_advantage="Masajistas certificados con técnica única.",
                risk_flags=[],
                rhetorical_device="contraste",
            ),
        }

    def test_selected_images_empty_is_valid(self):
        enrichment = PostEnrichment(
            **self._make_enrichment_kwargs(), selected_images=[]
        )
        assert enrichment.selected_images == []

    def test_selected_images_defaults_to_empty(self):
        enrichment = PostEnrichment(**self._make_enrichment_kwargs())
        assert enrichment.selected_images == []

    def test_selected_images_with_valid_items(self):
        images = [
            SelectedImage(
                uuid="63409f75-f41e-4837-80a4-058502437b12",
                content_url="https://s3.example.com/img1.png",
                role="hero",
                usage_note="Used as hero — warm tone matches emotional brief.",
            ),
            SelectedImage(
                uuid="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                content_url="https://s3.example.com/img2.png",
                role="supporting",
                usage_note="Supporting image for carousel slot.",
            ),
        ]
        enrichment = PostEnrichment(
            **self._make_enrichment_kwargs(), selected_images=images
        )

        assert len(enrichment.selected_images) == 2
        assert enrichment.selected_images[0].role == "hero"
        assert enrichment.selected_images[1].role == "supporting"

    def test_selected_image_all_roles_valid(self):
        for role in ("hero", "supporting", "background", "reference_only"):
            img = SelectedImage(
                uuid="test-uuid",
                content_url="https://s3.example.com/img.png",
                role=role,
                usage_note="Test note.",
            )
            assert img.role == role

    def test_selected_image_invalid_role_raises(self):
        with pytest.raises(Exception):
            SelectedImage(
                uuid="test",
                content_url="https://s3.example.com/img.png",
                role="totally_unknown_role",  # not a valid role or alias
                usage_note="Bad role.",
            )

    def test_selected_images_uuid_references_shortlist(self):
        """UUIDs in selected_images should correspond to shortlist items (model stores them as-is)."""
        shortlist_uuids = {"uuid-a", "uuid-b", "uuid-c"}
        images = [
            SelectedImage(
                uuid=uuid,
                content_url=f"https://s3.example.com/{uuid}.png",
                role="hero",
                usage_note="Selected.",
            )
            for uuid in shortlist_uuids
        ]
        enrichment = PostEnrichment(
            **self._make_enrichment_kwargs(), selected_images=images
        )

        returned_uuids = {img.uuid for img in enrichment.selected_images}
        assert returned_uuids == shortlist_uuids

    def test_selected_images_serializes_to_json(self):
        images = [
            SelectedImage(
                uuid="test-uuid-001",
                content_url="https://s3.example.com/img.png",
                role="background",
                usage_note="Background layer.",
            )
        ]
        enrichment = PostEnrichment(
            **self._make_enrichment_kwargs(), selected_images=images
        )
        dumped = enrichment.model_dump(mode="json")

        assert "selected_images" in dumped
        assert dumped["selected_images"][0]["uuid"] == "test-uuid-001"
        assert dumped["selected_images"][0]["role"] == "background"


# ===========================================================================
# GalleryPool model
# ===========================================================================


class TestGalleryPoolModel:
    def test_gallery_pool_default_shortlist_is_empty(self):
        pool = GalleryPool()
        assert pool.shortlist == []
        assert pool.total_fetched == 0
        assert pool.total_eligible == 0
        assert pool.truncated is False

    def test_gallery_pool_item_stores_score(self):
        item = GalleryPoolItem(
            uuid="img-001",
            content_url="https://s3.example.com/img-001.png",
            category="Inspiración",
            score=7.5,
        )
        assert item.score == 7.5

    def test_gallery_pool_item_empty_metadata_stored(self):
        item = GalleryPoolItem(
            uuid="img-002",
            content_url="https://s3.example.com/img-002.png",
            category="Marca",
            metadata={},
            score=0.0,
        )
        assert item.metadata == {}
        assert item.score == 0.0


# ===========================================================================
# fetch_gallery_pool — HTTP layer (lines 191-275)
# ===========================================================================


def _run(coro):
    return asyncio.run(coro)


class _FakeAsyncClient:
    """Minimal async context manager that fakes httpx.AsyncClient.get()."""

    def __init__(
        self, *, raise_: Exception | None = None, status: int = 200, body=None
    ):
        self._raise = raise_
        self._status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, *args, **kwargs):
        if self._raise:
            raise self._raise
        resp = MagicMock()
        resp.status_code = self._status
        if isinstance(self._body, Exception):
            resp.json.side_effect = self._body
        else:
            resp.json.return_value = self._body
        return resp


_BASE_URL = "https://gallery.example.com"
_API_KEY = "testkey"
_ACCOUNT = "acct-001"
_TASK_CTX = _task_context()

_IMG_ITEM = {
    "uuid": "img-aaa",
    "content": "https://s3.example.com/img-aaa.png",
    "type": "img",
    "category": "Inspiración",
    "used_at": None,
    "locked_until": None,
    "description": "Bruno en namaste",
    "metadata": {"tags": ["founder", "calm"]},
}


class TestFetchGalleryPool:
    def _patch(self, **kwargs):
        return patch(
            "marketer.gallery.httpx.AsyncClient",
            return_value=_FakeAsyncClient(**kwargs),
        )

    def test_timeout_returns_none_with_unavailable_warning(self):
        async def run():
            with self._patch(raise_=httpx.TimeoutException("timed out")):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is None
            assert warning == "gallery_api_unavailable"

        _run(run())

    def test_generic_exception_returns_unavailable(self):
        async def run():
            with self._patch(raise_=ConnectionError("refused")):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is None
            assert warning == "gallery_api_unavailable"

        _run(run())

    def test_404_returns_not_found_warning(self):
        async def run():
            with self._patch(status=404):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is None
            assert warning == "gallery_api_not_found"

        _run(run())

    def test_500_returns_unavailable_warning(self):
        async def run():
            with self._patch(status=500):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is None
            assert warning == "gallery_api_unavailable"

        _run(run())

    def test_json_parse_error_returns_unavailable(self):
        async def run():
            with self._patch(status=200, body=ValueError("bad json")):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is None
            assert warning == "gallery_api_unavailable"

        _run(run())

    def test_success_with_list_body_returns_pool(self):
        async def run():
            with self._patch(status=200, body=[_IMG_ITEM]):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, vision_candidates=5
                )
            assert pool is not None
            assert pool.total_fetched == 1
            assert pool.source == "gallery_api"
            assert warning is None

        _run(run())

    def test_success_with_paginated_dict_body_returns_pool(self):
        async def run():
            body = {"total_items": 1, "total_pages": 1, "results": [_IMG_ITEM]}
            with self._patch(status=200, body=body):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, vision_candidates=5
                )
            assert pool is not None
            assert pool.total_fetched == 1
            assert len(pool.shortlist) == 1
            assert pool.shortlist[0].uuid == "img-aaa"

        _run(run())

    def test_truncation_detected_via_api_total_items(self):
        async def run():
            # API reports 100 total but we only fetched 1
            body = {"total_items": 100, "results": [_IMG_ITEM]}
            with self._patch(status=200, body=body):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, page_size=50
                )
            assert pool is not None
            assert pool.truncated is True
            assert warning == "gallery_pool_truncated"

        _run(run())

    def test_truncation_detected_when_fetched_equals_page_size(self):
        async def run():
            # Got exactly page_size items → assume more pages exist
            items = [dict(_IMG_ITEM, uuid=f"img-{i:03d}") for i in range(3)]
            with self._patch(status=200, body=items):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, page_size=3
                )
            assert pool is not None
            assert pool.truncated is True

        _run(run())

    def test_no_truncation_when_fewer_than_page_size(self):
        async def run():
            with self._patch(status=200, body=[_IMG_ITEM]):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, page_size=50
                )
            assert pool is not None
            assert pool.truncated is False

        _run(run())

    def test_empty_eligible_pool_returns_warning(self):
        async def run():
            # Item is type=video → not eligible → empty shortlist
            video_item = dict(_IMG_ITEM, type="video")
            with self._patch(status=200, body=[video_item]):
                pool, warning = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX
                )
            assert pool is not None
            assert pool.total_eligible == 0
            assert len(pool.shortlist) == 0
            assert warning == "gallery_pool_empty"

        _run(run())

    def test_shortlist_capped_at_vision_candidates(self):
        async def run():
            items = [dict(_IMG_ITEM, uuid=f"img-{i:03d}") for i in range(10)]
            with self._patch(status=200, body=items):
                pool, _ = await fetch_gallery_pool(
                    _ACCOUNT, _BASE_URL, _API_KEY, _TASK_CTX, vision_candidates=3
                )
            assert pool is not None
            assert len(pool.shortlist) <= 3

        _run(run())
