"""Normalizer unit tests against fixtures and small synthetic envelopes."""

from __future__ import annotations

import json
from pathlib import Path

from marketer.normalizer import normalize

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENVELOPES = ROOT / "fixtures" / "envelopes"
FIXTURE_LEGACY = ROOT / "legacy" / "fixtures" / "envelopes"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ENVELOPES / name).read_text(encoding="utf-8"))


def _load_legacy(name: str) -> dict:
    return json.loads((FIXTURE_LEGACY / name).read_text(encoding="utf-8"))


def test_casa_maruja_spanish_fields_and_gallery_roles():
    data = _load("casa_maruja_post.json")
    ctx, warnings = normalize(data)
    assert ctx.brief is not None
    assert ctx.brief.business_name == "Casa Maruja"
    assert ctx.brief.communication_language == "spanish"
    assert "Ruzafa" in (ctx.brief.keywords or [])
    roles_by_url = {g.url: g.role for g in ctx.gallery}
    assert roles_by_url.get("https://cdn.example/casamaruja/hero.jpg") == "brand_asset"
    assert roles_by_url.get("https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg") == "content"
    codes = {w.code for w in warnings}
    assert "gallery_empty" not in codes


def test_fontaneria_top_level_brief_string_and_extras():
    data = _load_legacy("fontaneria_web.json")
    ctx, _warnings = normalize(data)
    assert ctx.brief is not None
    assert ctx.brief.business_name == "Fontaneria Rodriguez"
    assert ctx.brief.brief_background is not None
    assert "urgencias" in ctx.brief.brief_background.lower()
    extras = ctx.brief.extras
    assert "services" in extras
    assert isinstance(extras["services"], list)
    assert "reviews" in extras
    assert extras.get("reference_urls")


def test_rich_web_reviews_special_requests_reference_urls_in_extras():
    data = _load_legacy("rich_web_with_extras.json")
    ctx, _warnings = normalize(data)
    assert ctx.brief is not None
    ex = ctx.brief.extras
    assert ex.get("reviews")
    assert ex.get("special_requests")
    assert ex.get("reference_urls")
    assert ex.get("custom_metric_visits_last_month") == 12450
    ref_urls = {g.url for g in ctx.gallery if g.role == "reference"}
    assert "https://cdn.example/extrarich/ref-board.jpg" in ref_urls


def test_minimal_post_tiny_brief():
    data = _load("minimal_post.json")
    ctx, warnings = normalize(data)
    assert ctx.brief is not None
    assert ctx.brief.business_name == "Minimal SL"
    codes = {w.code for w in warnings}
    assert "gallery_empty" in codes


def test_missing_brief_warns_brief_missing():
    data = _load("missing_brief_post.json")
    ctx, warnings = normalize(data)
    assert ctx.brief is None
    assert any(w.code == "brief_missing" for w in warnings)


def test_edit_post_no_id_emits_context_missing_id():
    data = _load("edit_post_no_id.json")
    ctx, warnings = normalize(data)
    assert ctx.post_id is None
    assert any(w.code == "context_missing_id" for w in warnings)


def test_empty_sentinel_cleans_field_large_answer():
    base = _load("minimal_post.json")
    gate = base["payload"]["action_execution_gates"]["brief"]["response"]["data"]
    gate["brief"]["form_values"]["FIELD_LARGE_ANSWER"] = "  NINGUNO  "
    ctx, _warnings = normalize(base)
    assert ctx.brief is not None
    assert ctx.brief.business_description is None


def test_brief_request_mismatch_warning_when_divergent():
    base = _load("minimal_post.json")
    gate = base["payload"]["action_execution_gates"]["brief"]["response"]["data"]
    gate["brief"]["brief"] = (
        "Completamente distinto hablar de robots industriales en fábricas automatizadas sin relación con hostelería."
    )
    ctx, warnings = normalize(base)
    assert any(w.code == "brief_request_mismatch" for w in warnings)


def test_brand_tokens_and_channels_extracted_from_casa_maruja():
    data = _load("casa_maruja_post.json")
    ctx, _ = normalize(data)
    palette = ctx.brand_tokens.palette
    assert palette == ["#8b5a2b", "#d4a017", "#556b2f"]
    channel_kinds = {c.channel for c in ctx.available_channels}
    assert "website" in channel_kinds
    assert "dm" in channel_kinds
    assert "link_sticker" in channel_kinds
    website = next(c for c in ctx.available_channels if c.channel == "website")
    assert website.url_or_handle == "https://www.casamaruja.es"


def test_brief_facts_extracts_prices_and_urls_from_casa_maruja():
    data = _load("casa_maruja_post.json")
    ctx, _ = normalize(data)
    assert any("12" in p for p in ctx.brief_facts.prices)
    assert any("casamaruja.es" in u for u in ctx.brief_facts.urls)
    assert "#8b5a2b" in ctx.brief_facts.hex_colors


def test_requested_surface_format_detected_from_request():
    data = _load("casa_maruja_post.json")
    data["payload"]["client_request"]["description"] = (
        "Crea una historia destacando el plato de esta semana, con un sticker de enlace."
    )
    ctx, _ = normalize(data)
    assert ctx.requested_surface_format == "story"


def test_requested_surface_format_none_when_request_is_open():
    data = _load("casa_maruja_post.json")
    ctx, _ = normalize(data)
    assert ctx.requested_surface_format is None


def test_edit_post_without_prior_post_yields_none():
    data = _load("edit_post_no_id.json")
    ctx, _ = normalize(data)
    assert ctx.prior_post is None


def test_gallery_filters_bad_extension_and_keeps_order():
    base = _load("minimal_post.json")
    base["payload"]["action_execution_gates"]["gallery"] = {
        "passed": True,
        "reason": "ok",
        "status_code": 200,
        "response": {
            "status": "success",
            "data": [
                {
                    "url": "https://cdn.example/good.png",
                    "extension": "png",
                    "size": 1000,
                },
                {
                    "url": "https://cdn.example/bad.bmp",
                    "extension": "bmp",
                    "size": 1000,
                },
            ],
        },
    }
    ctx, warnings = normalize(base)
    urls = [g.url for g in ctx.gallery]
    assert "https://cdn.example/good.png" in urls
    assert "https://cdn.example/bad.bmp" not in urls
    assert any(w.code == "gallery_partially_filtered" for w in warnings)
