"""Validator unit tests for PostEnrichment v2."""

from __future__ import annotations

from marketer.schemas.enrichment import (
    BrandIntelligence,
    CallToAction,
    CaptionParts,
    Confidence,
    HashtagStrategy,
    ImageBrief,
    PostEnrichment,
    StrategicChoice,
    StrategicDecisions,
    VisualSelection,
)
from marketer.schemas.internal_context import (
    AvailableChannel,
    BrandTokens,
    BriefFacts,
    InternalContext,
)
from marketer.validator import validate_and_correct


def _ctx(
    *,
    action_code: str = "create_post",
    surface: str = "post",
    mode: str = "create",
    gallery_urls: list[tuple[str, str]] | None = None,
    palette: list[str] | None = None,
    channels: list[tuple[str, str | None]] | None = None,
    facts_urls: list[str] | None = None,
    facts_phones: list[str] | None = None,
    facts_prices: list[str] | None = None,
    requested_surface_format: str | None = None,
) -> InternalContext:
    from marketer.schemas.internal_context import GalleryItem

    gallery: list[GalleryItem] = []
    for url, role in gallery_urls or []:
        gallery.append(GalleryItem(url=url, role=role))  # type: ignore[arg-type]

    available = [
        AvailableChannel(channel=c, url_or_handle=v)  # type: ignore[arg-type]
        for c, v in (channels or [("dm", None), ("link_sticker", None)])
    ]

    return InternalContext(
        task_id="t1",
        callback_url="https://cb.example/x",
        action_code=action_code,  # type: ignore[arg-type]
        surface=surface,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        user_request="x " * 20,
        gallery=gallery,
        brand_tokens=BrandTokens(palette=[c.lower() for c in (palette or [])]),
        available_channels=available,
        brief_facts=BriefFacts(
            urls=[u.lower() for u in (facts_urls or [])],
            phones=facts_phones or [],
            prices=facts_prices or [],
        ),
        requested_surface_format=requested_surface_format,  # type: ignore[arg-type]
    )


def _base(**kwargs: object) -> PostEnrichment:
    defaults: dict[str, object] = dict(
        surface_format="post",
        content_pillar="product",
        title="Título del post",
        objective="Atraer reservas para este fin de semana",
        brand_dna="Bienvenidos a Test SL. Nuestra propuesta: cocina de mercado.",
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(
                chosen="post",
                alternatives_considered=["story"],
                rationale="Brief asks for product education",
            ),
            angle=StrategicChoice(
                chosen="transparencia de mercado",
                alternatives_considered=["urgencia", "behind the scenes"],
                rationale="Tag 'cocina honesta' en brief",
            ),
            voice=StrategicChoice(
                chosen="cercana, llana",
                alternatives_considered=["formal"],
                rationale="FIELD_COMMUNICATION_STYLE=friendly",
            ),
        ),
        visual_style_notes="Luz natural, mesa de madera, encuadre cercano.",
        narrative_connection=None,
        image=ImageBrief(
            concept="Plato de temporada en primer plano sobre madera.",
            generation_prompt="Close-up of a seasonal Spanish dish on rustic wood table, natural side light, 4:5.",
            alt_text="Primer plano de un plato de cocina de mercado.",
        ),
        caption=CaptionParts(
            hook="Esta semana, plato de mercado.",
            body="Producto de temporada en su punto. Cocina honesta.",
            cta_line="Reserva por DM.",
        ),
        cta=CallToAction(channel="dm", url_or_handle=None, label="Reserva por DM"),
        hashtag_strategy=HashtagStrategy(
            intent="local_discovery",
            suggested_volume=8,
            themes=["ruzafa", "cocina_de_mercado"],
        ),
        do_not=["no usar tipografía sobre la imagen"],
        visual_selection=VisualSelection(),
        confidence=Confidence(),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="local_food_service",
            funnel_stage_target="conversion",
            voice_register="cercano-honesto",
            emotional_beat="pertenencia",
            audience_persona="Vecino del barrio, 30-55, come fuera semanalmente.",
            unfair_advantage="Receta original del recetario de la abuela.",
            risk_flags=[],
            rhetorical_device="especificidad_concreta",
        ),
        cf_post_brief="El hook es un plato humeante. Caption:\nEsta semana, plato de mercado.\nProducto de temporada.\nReserva por DM.\nHashtags:\n#Test",
    )
    defaults.update(kwargs)
    return PostEnrichment(**defaults)  # type: ignore[arg-type]


def test_hallucinated_asset_urls_dropped():
    ctx = _ctx(gallery_urls=[("https://cdn.example/a.jpg", "content")])
    e = _base(
        visual_selection=VisualSelection(
            recommended_asset_urls=[
                "https://cdn.example/a.jpg",
                "https://evil.example/nope.jpg",
            ],
        )
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.visual_selection.recommended_asset_urls == ["https://cdn.example/a.jpg"]
    assert any(w.code == "visual_hallucinated" for w in warnings)


def test_reference_role_moved_from_assets_to_references():
    ctx = _ctx(
        gallery_urls=[
            ("https://cdn.example/a.jpg", "content"),
            ("https://cdn.example/ref.jpg", "reference"),
        ]
    )
    e = _base(
        visual_selection=VisualSelection(
            recommended_asset_urls=[
                "https://cdn.example/a.jpg",
                "https://cdn.example/ref.jpg",
            ],
        )
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert (
        "https://cdn.example/ref.jpg" not in out.visual_selection.recommended_asset_urls
    )
    assert (
        "https://cdn.example/ref.jpg" in out.visual_selection.recommended_reference_urls
    )
    assert any(w.code == "reference_used_as_asset" for w in warnings)


def test_palette_mismatch_scrubs_unknown_hex():
    ctx = _ctx(palette=["#aabbcc"])
    e = _base(
        visual_style_notes="Usar paleta cálida #aabbcc y un acento #ff00ff vibrante."
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert "#ff00ff" not in out.visual_style_notes
    assert "#aabbcc" in out.visual_style_notes
    assert any(w.code == "palette_mismatch" for w in warnings)


def test_url_in_caption_must_be_in_brief_facts():
    ctx = _ctx(facts_urls=["https://casamaruja.es"])
    e = _base(
        caption=CaptionParts(
            hook="x",
            body="Visita https://evil.example/free-pizza para más información.",
            cta_line="",
        )
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert "https://evil.example/free-pizza" not in out.caption.body
    assert any(w.code == "claim_not_in_brief" for w in warnings)


def test_cta_website_must_match_available_channels():
    ctx = _ctx(channels=[("website", "https://casamaruja.es"), ("dm", None)])
    e = _base(
        cta=CallToAction(
            channel="website", url_or_handle="https://other.com", label="Visita la web"
        )
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "none"
    assert any(w.code == "cta_channel_invalid" for w in warnings)


def test_cta_dm_clears_url_or_handle():
    ctx = _ctx()
    e = _base(
        cta=CallToAction(channel="dm", url_or_handle="should-not-be-here", label="DM")
    )
    out, _warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "dm"
    assert out.cta.url_or_handle is None


def test_requested_surface_format_overrides_llm_choice():
    ctx = _ctx(requested_surface_format="story")
    e = _base(surface_format="post")
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.surface_format == "story"
    assert any(w.code == "surface_format_overridden" for w in warnings)


def test_caption_length_exceeded_emits_warning():
    ctx = _ctx()
    e = _base(caption=CaptionParts(hook="x" * 200, body="ok", cta_line=""))
    _out, warnings, _ = validate_and_correct(e, ctx)
    assert any(
        w.code == "caption_length_exceeded" and w.field == "caption.hook"
        for w in warnings
    )


def test_price_not_in_brief_warns_but_does_not_scrub():
    ctx = _ctx(facts_prices=["12 €"])
    e = _base(caption=CaptionParts(hook="x", body="Menú a 99 €.", cta_line=""))
    out, warnings, _ = validate_and_correct(e, ctx)
    assert "99 €" in out.caption.body
    assert any(w.code == "price_not_in_brief" for w in warnings)


def test_do_not_truncates_to_five():
    ctx = _ctx()
    e = _base(do_not=["a", "b", "c", "d", "e", "f", "g"])
    out, warnings, _ = validate_and_correct(e, ctx)
    assert len(out.do_not) == 5
    assert any(w.code == "do_not_truncated" for w in warnings)


# ---------------------------------------------------------------------------
# _validate_cta — correction paths (lines 225-298)
# ---------------------------------------------------------------------------

def test_cta_channel_not_in_available_corrected_to_none():
    # LLM picked a channel that isn't in available_channels → corrected to "none"
    ctx = _ctx(channels=[("dm", None)])
    e = _base(cta=CallToAction(channel="website", url_or_handle="https://x.com", label="Web"))
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "none"
    assert out.cta.url_or_handle is None
    assert any(w.code == "cta_channel_invalid" for w in warnings)


def test_cta_phone_mismatching_number_corrected_to_none():
    ctx = _ctx(channels=[("phone", "+34 600 000 001"), ("dm", None)])
    e = _base(
        cta=CallToAction(channel="phone", url_or_handle="+34 699 999 999", label="Llámanos")
    )
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "none"
    assert out.cta.url_or_handle is None
    assert any(w.code == "cta_channel_invalid" for w in warnings)


def test_cta_website_null_url_corrected_to_none():
    # channel=website requires url_or_handle; if missing → corrected to "none"
    ctx = _ctx(channels=[("website", "https://example.com"), ("dm", None)])
    e = _base(cta=CallToAction(channel="website", url_or_handle=None, label="Web"))
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "none"
    assert any(w.code == "cta_channel_invalid" for w in warnings)


def test_cta_website_malformed_url_corrected_to_none():
    # website CTA with a non-HTTP URL → corrected to "none"
    ctx = _ctx(channels=[("website", "not-a-url"), ("dm", None)])
    e = _base(cta=CallToAction(channel="website", url_or_handle="not-a-url", label="Web"))
    out, warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "none"
    assert out.cta.url_or_handle is None
    assert any(w.code == "cta_url_invalid" for w in warnings)


def test_cta_link_sticker_clears_url_or_handle():
    ctx = _ctx(channels=[("link_sticker", None)])
    e = _base(
        cta=CallToAction(channel="link_sticker", url_or_handle="https://leftover.com", label="Sticker")
    )
    out, _warnings, _ = validate_and_correct(e, ctx)
    assert out.cta.channel == "link_sticker"
    assert out.cta.url_or_handle is None


# ---------------------------------------------------------------------------
# _check_cta_caption_coherence — lines 167-211
# ---------------------------------------------------------------------------

def test_cta_none_with_actionable_cta_line_warns():
    # cta.channel="none" but cta_line mentions "web" → mismatch warning
    ctx = _ctx(channels=[("dm", None)])
    e = _base(
        cta=CallToAction(channel="none", url_or_handle=None, label=""),
        caption=CaptionParts(
            hook="Hook.",
            body="Body.",
            cta_line="Visítanos en nuestra web para más info.",
        ),
    )
    _out, warnings, _ = validate_and_correct(e, ctx)
    assert any(w.code == "cta_caption_channel_mismatch" for w in warnings)


def test_cta_dm_but_cta_line_references_website_warns():
    ctx = _ctx(channels=[("dm", None), ("website", "https://example.com")])
    e = _base(
        cta=CallToAction(channel="dm", url_or_handle=None, label="DM"),
        caption=CaptionParts(
            hook="Hook.",
            body="Body.",
            cta_line="Reserva en nuestra web o tienda online.",
        ),
    )
    _out, warnings, _ = validate_and_correct(e, ctx)
    assert any(w.code == "cta_caption_channel_mismatch" for w in warnings)


def test_cta_dm_with_dm_cta_line_no_mismatch_warning():
    ctx = _ctx(channels=[("dm", None)])
    e = _base(
        cta=CallToAction(channel="dm", url_or_handle=None, label="DM"),
        caption=CaptionParts(
            hook="Hook.", body="Body.", cta_line="Mándanos un mensaje directo."
        ),
    )
    _out, warnings, _ = validate_and_correct(e, ctx)
    assert not any(w.code == "cta_caption_channel_mismatch" for w in warnings)


def test_cta_none_with_non_actionable_cta_line_no_warning():
    ctx = _ctx(channels=[("dm", None)])
    e = _base(
        cta=CallToAction(channel="none", url_or_handle=None, label=""),
        caption=CaptionParts(hook="Hook.", body="Body.", cta_line="Gracias por estar ahí."),
    )
    _out, warnings, _ = validate_and_correct(e, ctx)
    assert not any(w.code == "cta_caption_channel_mismatch" for w in warnings)
