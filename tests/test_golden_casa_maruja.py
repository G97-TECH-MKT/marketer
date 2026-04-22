"""Golden test: casa_maruja_post fixture vs baseline.

Live test (opt-in). Runs the full reason() pipeline with a real Gemini call and
asserts the deterministic fields match the frozen baseline at
`golden/posts/casa_maruja_v1.json`. Generative fields (captions, image prompts,
rationales) are only sanity-checked for shape/length/language markers.

Skipped unless `GEMINI_API_KEY` is set AND `MARKETER_RUN_LIVE=1`. Keep it off
in CI by default to avoid burning tokens.

Run locally with:
    MARKETER_RUN_LIVE=1 pytest tests/test_golden_casa_maruja.py -s
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from marketer.config import load_settings
from marketer.llm.gemini import GeminiClient
from marketer.reasoner import reason

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE = TESTS_DIR / "fixtures" / "envelopes" / "casa_maruja_post.json"
GOLDEN = TESTS_DIR / "golden" / "posts" / "casa_maruja_v1.json"


def _has_key() -> bool:
    if os.environ.get("GEMINI_API_KEY"):
        return True
    try:
        return bool(load_settings().gemini_api_key)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_has_key() and os.environ.get("MARKETER_RUN_LIVE") == "1"),
    reason="Live test requires GEMINI_API_KEY (env or .env) and MARKETER_RUN_LIVE=1",
)


@pytest.fixture(scope="module")
def live_output() -> dict:
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = load_settings()
    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    callback = reason(
        envelope, gemini=client, extras_truncation=settings.extras_list_truncation
    )
    return callback.model_dump(mode="json")


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# ---------- Deterministic: must match exactly -------------------------------


def test_status_completed(live_output):
    assert live_output["status"] == "COMPLETED"
    assert live_output["error_message"] is None


def test_schema_shape(live_output, golden):
    """The response must carry the same top-level shape as the baseline."""
    assert set(live_output.keys()) == set(golden.keys())
    assert set(live_output["output_data"].keys()) == set(golden["output_data"].keys())
    assert set(live_output["output_data"]["enrichment"].keys()) == set(
        golden["output_data"]["enrichment"].keys()
    )


def test_schema_version(live_output):
    assert live_output["output_data"]["enrichment"]["schema_version"] == "2.0"


def test_surface_and_pillar(live_output, golden):
    e = live_output["output_data"]["enrichment"]
    g = golden["output_data"]["enrichment"]
    assert e["surface_format"] == g["surface_format"] == "post"
    assert e["content_pillar"] == g["content_pillar"] == "product"


def test_cta_channel_dm(live_output):
    """Voice=friendly/cercano + dm available → channel must be dm."""
    cta = live_output["output_data"]["enrichment"]["cta"]
    assert cta["channel"] == "dm", f"expected dm, got {cta['channel']}"
    assert cta["url_or_handle"] is None
    assert cta["label"], "cta.label must be non-empty"


def test_visual_selection_assets(live_output, golden):
    """The plato_semana image should be recommended; equipo image avoided."""
    vs = live_output["output_data"]["enrichment"]["visual_selection"]
    assert (
        "https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg"
        in vs["recommended_asset_urls"]
    )
    assert "https://cdn.example/casamaruja/equipo.jpg" in vs["avoid_asset_urls"]


def test_hashtag_intent_local(live_output):
    hs = live_output["output_data"]["enrichment"]["hashtag_strategy"]
    assert hs["intent"] == "local_discovery"
    assert 5 <= hs["suggested_volume"] <= 15


def test_no_warnings_or_repairs(live_output):
    out = live_output["output_data"]
    assert out["warnings"] == [], f"unexpected warnings: {out['warnings']}"
    assert out["trace"]["repair_attempted"] is False
    assert out["trace"]["degraded"] is False


def test_confidence_floors(live_output):
    """Given how strong the casa_maruja brief is, no choice should be 'low'."""
    conf = live_output["output_data"]["enrichment"]["confidence"]
    for key, level in conf.items():
        assert level in ("high", "medium"), (
            f"confidence.{key}={level}, expected high/medium"
        )


def test_gallery_stats(live_output, golden):
    stats = live_output["output_data"]["trace"]["gallery_stats"]
    golden_stats = golden["output_data"]["trace"]["gallery_stats"]
    assert stats["accepted_count"] == golden_stats["accepted_count"]
    assert stats["rejected_count"] == golden_stats["rejected_count"]
    assert stats["truncated"] is False


# ---------- Generative: sanity checks on shape/language ---------------------


def test_caption_nonempty_and_in_spanish(live_output):
    cap = live_output["output_data"]["enrichment"]["caption"]
    assert cap["hook"].strip()
    assert cap["body"].strip()
    assert cap["cta_line"].strip()
    lower = (cap["hook"] + cap["body"] + cap["cta_line"]).lower()
    assert any(
        token in lower
        for token in ("mesa", "mercado", "plato", "ruzafa", "casa maruja")
    ), "caption lost the casa_maruja anchors"


def test_caption_length_caps(live_output):
    cap = live_output["output_data"]["enrichment"]["caption"]
    assert len(cap["hook"]) <= 125
    total = len(cap["hook"]) + len(cap["body"]) + len(cap["cta_line"])
    assert total <= 2200


def test_image_prompt_has_concrete_direction(live_output):
    img = live_output["output_data"]["enrichment"]["image"]
    prompt = img["generation_prompt"].lower()
    assert img["concept"].strip()
    assert img["alt_text"].strip()
    assert any(
        kw in prompt
        for kw in ("close-up", "close up", "plato", "dish", "bowl", "table")
    ), "image.generation_prompt lost concrete subject cues"


def test_strategic_decisions_have_rationale(live_output):
    sd = live_output["output_data"]["enrichment"]["strategic_decisions"]
    for key in ("surface_format", "angle", "voice"):
        choice = sd[key]
        assert choice["chosen"].strip()
        assert len(choice["rationale"]) >= 20, f"{key}.rationale too short"


def test_do_not_list_bounds(live_output):
    do_not = live_output["output_data"]["enrichment"]["do_not"]
    assert 1 <= len(do_not) <= 5
    for item in do_not:
        assert item.strip()


# ---------- Coherence: the fix we just shipped ------------------------------


def test_cta_channel_matches_caption(live_output):
    """Regression guard for the 'DM o web' bug we fixed in prompts+validator."""
    enrichment = live_output["output_data"]["enrichment"]
    cta_channel = enrichment["cta"]["channel"]
    cta_line = enrichment["caption"]["cta_line"].lower()
    if cta_channel == "dm":
        assert "web" not in cta_line and "sitio web" not in cta_line, (
            f"cta.channel=dm but cta_line mentions web: {cta_line!r}"
        )
    if cta_channel == "website":
        assert " dm" not in cta_line and "mensaje directo" not in cta_line, (
            f"cta.channel=website but cta_line mentions dm: {cta_line!r}"
        )


# ---------- Brand intelligence (the "alma") -------------------------------


def test_brand_intelligence_populated(live_output):
    """All 8 internal reasoning fields must be filled for a rich brief."""
    bi = live_output["output_data"]["enrichment"]["brand_intelligence"]
    required = {
        "business_taxonomy",
        "funnel_stage_target",
        "voice_register",
        "emotional_beat",
        "audience_persona",
        "unfair_advantage",
        "risk_flags",
        "rhetorical_device",
    }
    assert set(bi.keys()) >= required
    # Non-empty strings for free-text fields
    for key in required - {"risk_flags"}:
        assert isinstance(bi[key], str) and bi[key].strip(), f"{key} empty"


def test_brand_intelligence_taxonomy_for_restaurant(live_output):
    """Casa Maruja is a local restaurant — taxonomy must reflect that."""
    bi = live_output["output_data"]["enrichment"]["brand_intelligence"]
    taxo = bi["business_taxonomy"].lower()
    assert "food" in taxo or "restaurant" in taxo or "local" in taxo, (
        f"expected food/restaurant/local taxonomy, got {taxo!r}"
    )


def test_brand_intelligence_funnel_stage_is_enum(live_output):
    bi = live_output["output_data"]["enrichment"]["brand_intelligence"]
    assert bi["funnel_stage_target"] in {
        "awareness",
        "consideration",
        "conversion",
        "retention",
        "advocacy",
    }


def test_brand_intelligence_audience_persona_has_objection(live_output):
    """Prompt asks for archetype + objection. Check for objection presence."""
    bi = live_output["output_data"]["enrichment"]["brand_intelligence"]
    persona = bi["audience_persona"].lower()
    assert any(
        m in persona
        for m in ("objeción", "objecion", "objection", "duda", "miedo", "preocupa")
    ), f"audience_persona lacks objection signal: {persona!r}"


def test_brand_dna_present_and_sized(live_output):
    """brand_dna must be a non-empty narrative in the 100-1500 word range."""
    dna = live_output["output_data"]["enrichment"]["brand_dna"]
    assert isinstance(dna, str) and dna.strip()
    word_count = len(dna.split())
    assert 80 <= word_count <= 1500, (
        f"brand_dna has {word_count} words (target 100-1500)"
    )


def test_brand_dna_contains_required_sections(live_output):
    """Structure: CLIENT DNA header + Colors + Design Style + Typography + Contact
    (design-system reference format — current schema, April 2026)."""
    dna = live_output["output_data"]["enrichment"]["brand_dna"].lower()
    assert "client dna" in dna, "brand_dna missing 'CLIENT DNA' header"
    assert "colors" in dna, "brand_dna missing 'Colors' section"
    assert "design style" in dna, "brand_dna missing 'Design Style' section"
    assert "typography" in dna, "brand_dna missing 'Typography' section"
    assert "contact" in dna, "brand_dna missing 'Contact' section"


def test_brand_dna_business_name_present(live_output):
    """Casa Maruja must name itself at the top of its dna."""
    dna = live_output["output_data"]["enrichment"]["brand_dna"]
    assert "Casa Maruja" in dna


def test_brand_dna_references_brand_palette(live_output):
    """Estilo visual block should reference at least one brand palette hex."""
    dna = live_output["output_data"]["enrichment"]["brand_dna"].lower()
    # Casa Maruja palette: 8B5A2B, D4A017, 556B2F
    palette_hits = sum(1 for hx in ("#8b5a2b", "#d4a017", "#556b2f") if hx in dna)
    assert palette_hits >= 1, (
        f"brand_dna did not cite any brand palette hex: {dna[:200]!r}"
    )


def test_brand_dna_does_not_invent_contacts(live_output):
    """brand_dna must not contain URLs/phones/emails absent from brief_facts.

    The validator scrubs hallucinated tokens and emits a warning. If
    claim_not_in_brief appears among warnings, that's the regression.
    """
    warnings = [w["code"] for w in live_output["output_data"]["warnings"]]
    claim_violations = [w for w in warnings if w == "claim_not_in_brief"]
    assert not claim_violations, (
        f"brand_dna invented contact tokens: {claim_violations}"
    )


def test_brand_intelligence_unfair_advantage_nonempty(live_output):
    """For a rich brief like Casa Maruja, unfair_advantage MUST NOT be
    'dato insuficiente en el brief' — there is plenty of signal."""
    bi = live_output["output_data"]["enrichment"]["brand_intelligence"]
    adv = bi["unfair_advantage"].lower()
    assert "dato insuficiente" not in adv, (
        f"unfair_advantage fell back to placeholder on a rich brief: {adv!r}"
    )
