"""Unit tests for USP Memory Gateway integration in normalizer.

All tests mock fetch_user_profile — no network calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketer.normalizer import normalize
from marketer.user_profile import IdentityData, UserInsight, UserProfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENVELOPES = ROOT / "tests" / "fixtures" / "envelopes"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ENVELOPES / name).read_text(encoding="utf-8"))


def _make_identity(
    *,
    name: str = "UP Business",
    category: str = "UP Category",
    country: str = "Colombia",
    history: str = "Founded in 2020",
    target_customer: str = "Young professionals",
    website_url: str = "https://example.com",
    comm_style: str = "íntimo y elegante",
    comm_lang: str = "spanish",
    colors: list[str] | None = None,
    keywords: str = "relax,wellness",
    has_material: bool = True,
    font: str = "Montserrat",
    design_style: str = "minimal",
    post_content_style: str = "educational",
    logo_url: str = "https://example.com/logo.png",
    subcategory: str = "Spa",
    store_type: str = "physical",
    location: str = "Bogotá",
    instagram_url: str = "https://instagram.com/example",
    facebook_url: str = "",
    phone: str = "+573001234567",
    email: str = "hello@example.com",
) -> IdentityData:
    return IdentityData(
        uuid="abc-123",
        account_uuid="f7a8b9c0-d1e2-4f3a-ab4b-5c6d7e8f9012",
        brand={
            "colors": colors if colors is not None else ["#5E204D", "#9C7945"],
            "communicationLang": comm_lang,
            "communicationStyle": comm_style,
            "designStyle": design_style,
            "font": font,
            "hasMaterial": has_material,
            "keywords": keywords,
            "postContentStyle": post_content_style,
            "logoUrl": logo_url,
        },
        company={
            "name": name,
            "category": category,
            "subcategory": subcategory,
            "country": country,
            "businessPhone": phone,
            "email": email,
            "websiteUrl": website_url,
            "historyAndFounder": history,
            "targetCustomer": target_customer,
            "productServices": "Massage, spa treatments",
            "storeType": store_type,
            "location": location,
        },
        social_media={
            "instagramUrl": instagram_url,
            "facebookUrl": facebook_url or None,
            "tiktokUrl": None,
            "linkedinUrl": None,
        },
    )


def _make_profile(identity: IdentityData | None = None, insights: list[UserInsight] | None = None) -> UserProfile:
    return UserProfile(
        identity=identity or _make_identity(),
        insights=insights or [],
        fetched_at="2026-04-22T00:00:00Z",
    )


def _make_insight(
    key: str = "audience_peak_hours",
    insight: str = "La audiencia interactúa más entre 19:00 y 21:00 los jueves",
    confidence: int = 85,
    active: bool = True,
) -> UserInsight:
    return UserInsight(
        key=key,
        insight=insight,
        confidence=confidence,
        source_identifier="instagram_analytics",
        updated_at="2026-04-21T08:00:00Z",
    )


# ---------------------------------------------------------------------------
# test_up_overrides_brief_field_by_field
# ---------------------------------------------------------------------------

def test_up_overrides_brief_name():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(name="UP Override Name"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief is not None
    assert ctx.brief.business_name == "UP Override Name"


def test_up_overrides_brief_category():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(category="UP Category Override"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief.category == "UP Category Override"


def test_up_overrides_brief_country():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(country="Argentina"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief.country == "Argentina"


def test_up_overrides_brief_business_description():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(history="Founded by the UP source"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief.business_description == "Founded by the UP source"


def test_up_overrides_brief_tone():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(comm_style="profesional y cercano"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief.tone == "profesional y cercano"


def test_up_overrides_brief_communication_language():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(comm_lang="english"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief.communication_language == "english"


def test_up_overrides_brief_colors():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(colors=["#FF0000", "#00FF00"]))
    ctx, _ = normalize(data, user_profile=profile)
    assert "#FF0000" in ctx.brief.colors or "#ff0000" in [c.lower() for c in ctx.brief.colors]


# ---------------------------------------------------------------------------
# test_up_empty_field_does_not_wipe_brief
# ---------------------------------------------------------------------------

def test_up_empty_name_does_not_wipe_brief():
    data = _load("nubiex_golden_input.json")
    # Get the brief-only value first
    ctx_base, _ = normalize(data)
    brief_name_before = ctx_base.brief.business_name if ctx_base.brief else None

    # UP has empty name
    identity = _make_identity(name="")
    identity.company["name"] = ""
    profile = _make_profile(identity)
    ctx, _ = normalize(data, user_profile=profile)
    if brief_name_before:
        assert ctx.brief.business_name == brief_name_before


def test_up_empty_colors_does_not_wipe_palette():
    data = _load("nubiex_golden_input.json")
    ctx_base, _ = normalize(data)
    palette_before = ctx_base.brand_tokens.palette[:]

    identity = _make_identity(colors=[])
    profile = _make_profile(identity)
    ctx, _ = normalize(data, user_profile=profile)
    # Empty UP colors should not wipe the existing palette
    if palette_before:
        assert ctx.brand_tokens.palette == palette_before


# ---------------------------------------------------------------------------
# test_up_colors_override_palette_and_brief_facts
# ---------------------------------------------------------------------------

def test_up_colors_override_palette_and_brief_facts():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(colors=["#5E204D", "#9C7945"]))
    ctx, _ = normalize(data, user_profile=profile)

    # Palette should contain hex-validated versions
    assert "#5e204d" in ctx.brand_tokens.palette
    assert "#9c7945" in ctx.brand_tokens.palette

    # BriefFacts hex_colors should be rebuilt from merged colors
    assert "#5e204d" in ctx.brief_facts.hex_colors or "#5E204D" in ctx.brief.colors


# ---------------------------------------------------------------------------
# test_up_insights_added_to_context
# ---------------------------------------------------------------------------

def test_up_insights_added_to_context_active_only():
    data = _load("nubiex_golden_input.json")
    active = _make_insight(key="peak_hours", insight="Active insight", confidence=80)
    # Inactive insight — should be filtered in fetch_user_profile before reaching normalizer.
    # Here we only pass active ones (filtering happens at fetch time).
    profile = _make_profile(insights=[active])
    ctx, _ = normalize(data, user_profile=profile)

    assert len(ctx.user_insights) == 1
    assert ctx.user_insights[0]["key"] == "peak_hours"
    assert ctx.user_insights[0]["insight"] == "Active insight"


def test_up_insights_sorted_by_confidence_desc():
    data = _load("nubiex_golden_input.json")
    low = _make_insight(key="low", insight="Low confidence", confidence=30)
    high = _make_insight(key="high", insight="High confidence", confidence=90)
    # Already sorted in fetch_user_profile; here we pass pre-sorted list
    profile = _make_profile(insights=[high, low])
    ctx, _ = normalize(data, user_profile=profile)

    assert ctx.user_insights[0]["key"] == "high"
    assert ctx.user_insights[1]["key"] == "low"


# ---------------------------------------------------------------------------
# test_up_none_normalize_unchanged
# ---------------------------------------------------------------------------

def test_up_none_normalize_unchanged():
    data = _load("nubiex_golden_input.json")
    ctx_without, warnings_without = normalize(data)
    ctx_with_none, warnings_with_none = normalize(data, user_profile=None)

    # Core brief fields should be identical
    if ctx_without.brief and ctx_with_none.brief:
        assert ctx_without.brief.business_name == ctx_with_none.brief.business_name
        assert ctx_without.brief.category == ctx_with_none.brief.category
    assert ctx_with_none.user_insights == []


# ---------------------------------------------------------------------------
# test_up_brief_absent_up_present
# ---------------------------------------------------------------------------

def test_up_brief_absent_up_present():
    data = _load("missing_brief_post.json")
    ctx_without, w_without = normalize(data)
    assert ctx_without.brief is None

    profile = _make_profile(_make_identity(name="FlatBrief From UP"))
    ctx, warnings = normalize(data, user_profile=profile)

    # FlatBrief should be built from UP data alone
    assert ctx.brief is not None
    assert ctx.brief.business_name == "FlatBrief From UP"


# ---------------------------------------------------------------------------
# test_user_profile_unavailable_warning
# ---------------------------------------------------------------------------

def test_user_profile_unavailable_warning():
    data = _load("nubiex_golden_input.json")
    ctx, warnings = normalize(data, user_profile=None, usp_warning="user_profile_unavailable")
    codes = {w.code for w in warnings}
    assert "user_profile_unavailable" in codes


# ---------------------------------------------------------------------------
# test_user_profile_skipped_warning
# ---------------------------------------------------------------------------

def test_user_profile_skipped_warning_no_key():
    data = _load("nubiex_golden_input.json")
    ctx, warnings = normalize(data, user_profile=None, usp_warning="user_profile_skipped")
    codes = {w.code for w in warnings}
    assert "user_profile_skipped" in codes


def test_user_profile_not_found_warning():
    data = _load("nubiex_golden_input.json")
    profile = UserProfile(identity=None, insights=[], fetched_at="2026-04-22T00:00:00Z")
    ctx, warnings = normalize(data, user_profile=profile, usp_warning="user_profile_not_found")
    codes = {w.code for w in warnings}
    assert "user_profile_not_found" in codes


# ---------------------------------------------------------------------------
# extras — UP-only fields stored in FlatBrief.extras
# ---------------------------------------------------------------------------

def test_up_extras_populated():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(
        _make_identity(
            subcategory="Spa",
            store_type="physical",
            location="Bogotá",
            logo_url="https://example.com/logo.png",
        )
    )
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brief is not None
    assert ctx.brief.extras.get("subcategory") == "Spa"
    assert ctx.brief.extras.get("store_type") == "physical"
    assert ctx.brief.extras.get("location") == "Bogotá"
    assert ctx.brief.extras.get("logo_url") == "https://example.com/logo.png"


# ---------------------------------------------------------------------------
# channels — UP social URLs override available_channels
# ---------------------------------------------------------------------------

def test_up_instagram_url_overrides_channel():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(
        _make_identity(instagram_url="https://instagram.com/up_override")
    )
    ctx, _ = normalize(data, user_profile=profile)
    instagram_ch = next(
        (c for c in ctx.available_channels if c.channel == "instagram_profile"), None
    )
    assert instagram_ch is not None
    assert instagram_ch.url_or_handle == "https://instagram.com/up_override"


def test_up_brand_tokens_font_and_design_style():
    data = _load("nubiex_golden_input.json")
    profile = _make_profile(_make_identity(font="Playfair Display", design_style="luxury"))
    ctx, _ = normalize(data, user_profile=profile)
    assert ctx.brand_tokens.font_style == "Playfair Display"
    assert ctx.brand_tokens.design_style == "luxury"
