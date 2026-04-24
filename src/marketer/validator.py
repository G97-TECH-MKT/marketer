"""Post-generation validator for MARKETER output (v2 slim).

Deterministic-first per SPEC §9. Checks:
- Surface-format gating: when InternalContext.requested_surface_format is set,
  force the LLM choice to match.
- Hallucination guards on ALL text fields:
    * URLs   → must be in brief_facts.urls
    * hex    → must be in brand_tokens.palette  (palette_mismatch)
    * phones → must be in brief_facts.phones
    * emails → must be in brief_facts.emails
    * prices → if not in brief_facts.prices, warn (not block)
- CTA channel must be present in InternalContext.available_channels.
- Caption length per surface.
- CTA URL sanity (well-formed, http(s)).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from marketer.schemas.enrichment import (
    CallToAction,
    PostEnrichment,
    Warning,
)
from marketer.schemas.internal_context import InternalContext

_URL_RE = re.compile(r"https?://[^\s<>'\"]+")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s\-().]{6,}\d(?!\w)")
_PRICE_RE = re.compile(r"\d+[\d.,]*\s?(?:€|EUR|eur|euros|usd|USD|\$)")

# Per-surface caption caps (chars). Values are soft Instagram-safe targets.
_CAPTION_CAPS: dict[str, dict[str, int]] = {
    "post": {"hook": 125, "body": 1900, "cta_line": 180, "total": 2200},
    "story": {"hook": 80, "body": 220, "cta_line": 80, "total": 250},
    "reel": {"hook": 100, "body": 850, "cta_line": 150, "total": 1000},
    "carousel": {"hook": 125, "body": 1900, "cta_line": 180, "total": 2200},
}


def _normalize_phone(raw: str) -> str:
    return re.sub(r"[\s\-().]", "", raw)


def _scrub_token(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(token, "[…]")


def _check_text_facts(
    text: str,
    *,
    field: str,
    brief_facts_urls: set[str],
    brief_facts_phones: set[str],
    brief_facts_emails: set[str],
    brief_facts_prices: set[str],
    palette: set[str],
    warnings: list[Warning],
) -> str:
    """Scan a free-text field for tokens that must be in the brief. Returns scrubbed text."""
    if not text:
        return text
    scrubbed = text

    for url in _URL_RE.findall(text):
        clean = url.rstrip(".,;:)").lower()
        if clean not in brief_facts_urls:
            warnings.append(
                Warning(
                    code="claim_not_in_brief",
                    message=f"URL not in brief_facts: {clean}",
                    field=field,
                )
            )
            scrubbed = _scrub_token(scrubbed, url)

    for hx in _HEX_RE.findall(text):
        if hx.lower() not in palette:
            warnings.append(
                Warning(
                    code="palette_mismatch",
                    message=f"Hex {hx} not in brand palette",
                    field=field,
                )
            )
            scrubbed = _scrub_token(scrubbed, hx)

    for em in _EMAIL_RE.findall(text):
        if em.lower() not in brief_facts_emails:
            warnings.append(
                Warning(
                    code="claim_not_in_brief",
                    message=f"Email not in brief_facts: {em}",
                    field=field,
                )
            )
            scrubbed = _scrub_token(scrubbed, em)

    for ph in _PHONE_RE.findall(text):
        if _normalize_phone(ph) not in brief_facts_phones:
            warnings.append(
                Warning(
                    code="claim_not_in_brief",
                    message=f"Phone not in brief_facts: {ph}",
                    field=field,
                )
            )
            scrubbed = _scrub_token(scrubbed, ph)

    for pr in _PRICE_RE.findall(text):
        if pr.strip() not in brief_facts_prices:
            warnings.append(
                Warning(
                    code="price_not_in_brief",
                    message=f"Price {pr.strip()} not in brief_facts (verify before publishing)",
                    field=field,
                )
            )
            # Prices are NOT scrubbed — too aggressive; we warn only.

    return scrubbed


_CHANNEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dm": (
        "dm",
        "mensaje directo",
        "mensaje privado",
        "mándanos un mensaje",
        "escríbenos un mensaje",
    ),
    "website": (
        "web",
        "sitio web",
        "página web",
        "en la web",
        "visita nuestra web",
        "en nuestra web",
        "tienda online",
        "tienda en línea",
        "shop online",
    ),
    "link_sticker": (
        "sticker",
        "enlace de la bio",
        "link in bio",
        "toca el sticker",
        "bio link",
    ),
    "instagram_profile": ("perfil", "síguenos"),
    "facebook": ("facebook",),
    "tiktok": ("tiktok", "tik tok"),
    "linkedin": ("linkedin",),
    "phone": ("llámanos", "llama al", "teléfono", "llamada"),
    "whatsapp": ("whatsapp", "wasap"),
    "email": ("email", "correo", "escríbenos a"),
}


def _check_cta_caption_coherence(
    cta: CallToAction, cta_line: str, warnings: list[Warning]
) -> None:
    """Warn when caption.cta_line references a channel that conflicts with cta.channel."""
    if not cta_line:
        return
    text = cta_line.lower()
    mentioned: set[str] = set()
    for channel, keywords in _CHANNEL_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                mentioned.add(channel)
                break
    if cta.channel == "none":
        if mentioned:
            warnings.append(
                Warning(
                    code="cta_caption_channel_mismatch",
                    message=(
                        f"cta.channel='none' but caption.cta_line references channel(s) "
                        f"{sorted(mentioned)}. Use non-actionable language when no CTA channel exists."
                    ),
                    field="caption.cta_line",
                )
            )
        return
    foreign = mentioned - {cta.channel}
    if foreign:
        warnings.append(
            Warning(
                code="cta_caption_channel_mismatch",
                message=(
                    f"caption.cta_line references channel(s) {sorted(foreign)} but "
                    f"cta.channel='{cta.channel}'. Caption should name only the chosen channel."
                ),
                field="caption.cta_line",
            )
        )


def _validate_cta(
    cta: CallToAction, ctx: InternalContext, warnings: list[Warning]
) -> CallToAction:
    """Check the CTA channel is one we offered, and the url_or_handle matches."""
    channel_index: dict[str, set[str]] = {}
    for ch in ctx.available_channels:
        channel_index.setdefault(ch.channel, set())
        if ch.url_or_handle:
            channel_index[ch.channel].add(ch.url_or_handle)

    if cta.channel == "none":
        cta.url_or_handle = None
        return cta

    if cta.channel not in channel_index:
        warnings.append(
            Warning(
                code="cta_channel_invalid",
                message=f"Channel '{cta.channel}' not in available_channels",
                field="cta.channel",
            )
        )
        cta.channel = "none"
        cta.url_or_handle = None
        return cta

    if cta.channel in ("dm", "link_sticker"):
        cta.url_or_handle = None
        return cta

    if cta.channel in ("phone", "whatsapp") and cta.url_or_handle:
        if _normalize_phone(cta.url_or_handle) not in {
            _normalize_phone(x) for x in channel_index[cta.channel] if x
        }:
            warnings.append(
                Warning(
                    code="cta_channel_invalid",
                    message=f"Phone CTA value not in available_channels[{cta.channel}]",
                    field="cta.url_or_handle",
                )
            )
            cta.channel = "none"
            cta.url_or_handle = None
        return cta

    if cta.url_or_handle is None:
        warnings.append(
            Warning(
                code="cta_channel_invalid",
                message=f"channel '{cta.channel}' requires url_or_handle",
                field="cta.url_or_handle",
            )
        )
        cta.channel = "none"
        return cta

    candidate = cta.url_or_handle.lower()
    available = {(x or "").lower() for x in channel_index[cta.channel]}
    if candidate not in available:
        warnings.append(
            Warning(
                code="cta_channel_invalid",
                message=f"CTA value not in available_channels[{cta.channel}]: {cta.url_or_handle}",
                field="cta.url_or_handle",
            )
        )
        cta.channel = "none"
        cta.url_or_handle = None
        return cta

    parsed = urlparse(cta.url_or_handle)
    if cta.channel == "website" and (
        parsed.scheme not in ("http", "https") or not parsed.netloc
    ):
        warnings.append(
            Warning(
                code="cta_url_invalid",
                message=f"website CTA URL malformed: {cta.url_or_handle}",
                field="cta.url_or_handle",
            )
        )
        cta.channel = "none"
        cta.url_or_handle = None

    return cta


def validate_and_correct(
    enrichment: PostEnrichment, ctx: InternalContext
) -> tuple[PostEnrichment, list[Warning], list[str]]:
    """Apply deterministic checks. Returns (corrected, warnings, blocking_errors)."""
    warnings: list[Warning] = []
    blocking: list[str] = []

    # --- Deterministic surface override -----------------------------------------
    if (
        ctx.requested_surface_format
        and enrichment.surface_format != ctx.requested_surface_format
    ):
        warnings.append(
            Warning(
                code="surface_format_overridden",
                message=(
                    f"User asked for '{ctx.requested_surface_format}' explicitly; "
                    f"overriding LLM choice '{enrichment.surface_format}'"
                ),
                field="surface_format",
            )
        )
        enrichment.surface_format = ctx.requested_surface_format

    # --- Hallucination guards on text fields ------------------------------------
    facts = ctx.brief_facts
    palette = {h.lower() for h in ctx.brand_tokens.palette}
    facts_urls = {u.lower() for u in facts.urls}
    facts_phones = {_normalize_phone(p) for p in facts.phones if p}
    facts_emails = {e.lower() for e in facts.emails}
    facts_prices = {p for p in facts.prices}

    def scrub(field_name: str, text: str) -> str:
        return _check_text_facts(
            text,
            field=field_name,
            brief_facts_urls=facts_urls,
            brief_facts_phones=facts_phones,
            brief_facts_emails=facts_emails,
            brief_facts_prices=facts_prices,
            palette=palette,
            warnings=warnings,
        )

    enrichment.brand_dna = scrub("brand_dna", enrichment.brand_dna)
    enrichment.caption.hook = scrub("caption.hook", enrichment.caption.hook)
    enrichment.caption.body = scrub("caption.body", enrichment.caption.body)
    enrichment.caption.cta_line = scrub("caption.cta_line", enrichment.caption.cta_line)
    enrichment.cf_post_brief = scrub("cf_post_brief", enrichment.cf_post_brief)

    # --- CTA channel validation -------------------------------------------------
    enrichment.cta = _validate_cta(enrichment.cta, ctx, warnings)

    # --- CTA / caption coherence -----------------------------------------------
    _check_cta_caption_coherence(enrichment.cta, enrichment.caption.cta_line, warnings)

    # --- Caption length per surface --------------------------------------------
    caps = _CAPTION_CAPS.get(enrichment.surface_format, _CAPTION_CAPS["post"])
    parts = enrichment.caption
    total_len = len(parts.hook) + len(parts.body) + len(parts.cta_line)
    if len(parts.hook) > caps["hook"]:
        warnings.append(
            Warning(
                code="caption_length_exceeded",
                message=f"hook is {len(parts.hook)} chars (cap {caps['hook']} for {enrichment.surface_format})",
                field="caption.hook",
            )
        )
    if len(parts.body) > caps["body"]:
        warnings.append(
            Warning(
                code="caption_length_exceeded",
                message=f"body is {len(parts.body)} chars (cap {caps['body']} for {enrichment.surface_format})",
                field="caption.body",
            )
        )
    if len(parts.cta_line) > caps["cta_line"]:
        warnings.append(
            Warning(
                code="caption_length_exceeded",
                message=f"cta_line is {len(parts.cta_line)} chars (cap {caps['cta_line']})",
                field="caption.cta_line",
            )
        )
    if total_len > caps["total"]:
        warnings.append(
            Warning(
                code="caption_length_exceeded",
                message=f"total caption {total_len} chars (cap {caps['total']})",
                field="caption",
            )
        )

    # --- Presence checks (warnings only) ---------------------------------------
    if not enrichment.caption.hook.strip():
        warnings.append(
            Warning(
                code="field_missing", message="caption.hook empty", field="caption.hook"
            )
        )
    if not enrichment.caption.body.strip():
        warnings.append(
            Warning(
                code="field_missing", message="caption.body empty", field="caption.body"
            )
        )

    return enrichment, warnings, blocking
