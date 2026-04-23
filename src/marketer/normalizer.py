"""Normalizer: ROUTER envelope -> InternalContext.

See SPEC §5 for the behavior. This module is pure (no network, no LLM).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from marketer.schemas.enrichment import Warning
from marketer.schemas.envelope import RouterEnvelope
from marketer.schemas.internal_context import (
    ActionCode,
    AvailableChannel,
    BrandTokens,
    BriefFacts,
    ChannelKind,
    FlatBrief,
    GalleryItem,
    GalleryPool,
    ImageRole,
    InternalContext,
    Mode,
    PriorPost,
    SubscriptionJob,
    Surface,
    SurfaceFormat,
)
from marketer.user_profile import UserProfile

logger = logging.getLogger(__name__)

EMPTY_SENTINELS = {"", "ninguno", "ninguna", "none", "n/a", "-", "null"}
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}
MAX_SIZE_BYTES = 20 * 1024 * 1024
MAX_GALLERY_ITEMS = 20
IMAGE_CATALOG_GATE_HINTS = {"image_catalog", "gallery", "images", "media"}
ALLOWED_FORM_EXTRAS = {
    "FIELD_POST_CONTENT_STYLE",
    "FIELD_DESIGN_STYLE",
    "FIELD_FONT_STYLE",
}


def _nested_brief_dict(brief_data: dict[str, Any]) -> dict[str, Any]:
    """`data.brief` may be a nested brief object (form_values) or a plain string wish."""
    b = brief_data.get("brief")
    return b if isinstance(b, dict) else {}


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.lower() in EMPTY_SENTINELS:
        return None
    return stripped or None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = _clean_string(item)
            if cleaned:
                out.append(cleaned)
    return out


def _normalize_attachment_urls(value: Any) -> list[str]:
    """Accept new contract (list[str]) and tolerate legacy list[dict{url}]."""
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        candidate: str | None = None
        if isinstance(item, str):
            candidate = _clean_string(item)
        elif isinstance(item, dict):
            candidate = _clean_string(item.get("url"))
        if candidate:
            urls.append(candidate)
    return urls


def _first_non_empty(*values: Any) -> str | None:
    for v in values:
        cleaned = _clean_string(v)
        if cleaned:
            return cleaned
    return None


_KNOWN_ACTIONS = {
    "create_post",
    "edit_post",
    "create_web",
    "edit_web",
    "subscription_strategy",
    "create_prod_line",
}


def _parse_action_code(raw: str) -> tuple[ActionCode, Surface, Mode]:
    if raw not in _KNOWN_ACTIONS:
        raise ValueError(f"unsupported_action_code: {raw}")
    if raw == "subscription_strategy":
        return raw, "other", "create"  # type: ignore[return-value]
    surface: Surface = "web" if raw.endswith("_web") else "post"
    mode: Mode = "edit" if raw.startswith("edit_") else "create"
    return raw, surface, mode  # type: ignore[return-value]


def _extension_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
        if "." in path:
            return path.rsplit(".", 1)[-1].lower() or None
    except Exception:
        return None
    return None


def _is_allowed_image(
    extension: str | None, mime_type: str | None, url: str
) -> tuple[bool, str | None]:
    ext = (extension or "").lower() or None
    if ext in ALLOWED_EXTENSIONS:
        return True, ext
    if mime_type in ALLOWED_MIME_TYPES:
        derived = {"image/jpeg": "jpeg", "image/png": "png"}[mime_type]
        return True, derived
    suffix = _extension_from_url(url)
    if suffix in ALLOWED_EXTENSIONS:
        return True, suffix
    return False, None


def _sanitize_gallery_item(
    raw: dict[str, Any], default_role: ImageRole
) -> GalleryItem | None:
    url = raw.get("url")
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    extension = raw.get("extension")
    mime_type = raw.get("mime_type") or raw.get("mimetype")
    allowed, ext_final = _is_allowed_image(extension, mime_type, url)
    if not allowed:
        return None
    size = raw.get("size") or raw.get("size_bytes")
    if isinstance(size, int):
        if size == 0 or size > MAX_SIZE_BYTES:
            return None
    role_raw = raw.get("role")
    role: ImageRole = (
        role_raw
        if role_raw in ("brand_asset", "content", "reference", "unknown")
        else default_role
    )
    tags = _clean_list(raw.get("tags"))
    return GalleryItem(
        url=url,
        name=_clean_string(raw.get("name")),
        extension=ext_final,
        mime_type=mime_type if isinstance(mime_type, str) else None,
        size_bytes=size if isinstance(size, int) else None,
        width=raw.get("width") if isinstance(raw.get("width"), int) else None,
        height=raw.get("height") if isinstance(raw.get("height"), int) else None,
        tags=tags,
        description=_clean_string(raw.get("description")),
        category=_clean_string(raw.get("category")),
        role=role,
        used_previously=raw.get("used_previously")
        if isinstance(raw.get("used_previously"), bool)
        else None,
    )


def _collect_raw_images(
    payload: dict[str, Any],
) -> list[tuple[dict[str, Any], ImageRole]]:
    """Collect raw image dicts from every known source, tagged with default role."""
    collected: list[tuple[dict[str, Any], ImageRole]] = []

    # 1. Any gate that looks like an image catalog
    gates = payload.get("action_execution_gates") or {}
    for gate_code, gate_data in gates.items():
        if not isinstance(gate_data, dict):
            continue
        name = gate_code.lower() if isinstance(gate_code, str) else ""
        response = gate_data.get("response") or {}
        data = response.get("data") if isinstance(response, dict) else None
        items = _extract_image_list_from(data)
        if name in IMAGE_CATALOG_GATE_HINTS or (items and name != "brief"):
            for raw in items:
                collected.append((raw, "content"))

    # 2. attachments (new contract is list[str])
    client_request = payload.get("client_request") or {}
    for attachment_url in _normalize_attachment_urls(client_request.get("attachments")):
        collected.append(({"url": attachment_url}, "unknown"))

    # 3. FIELD_BRAND_MATERIAL from brief gate
    brief_gate = (gates.get("brief") or {}).get("response") or {}
    brief_data = brief_gate.get("data") if isinstance(brief_gate, dict) else None
    if isinstance(brief_data, dict):
        brief_obj = _nested_brief_dict(brief_data)
        form_values = brief_obj.get("form_values") or {}
        for raw in form_values.get("FIELD_BRAND_MATERIAL") or []:
            if isinstance(raw, dict) and raw.get("url"):
                collected.append((raw, "brand_asset"))

    # 4. top-level payload.images
    for raw in payload.get("images") or []:
        if isinstance(raw, dict) and raw.get("url"):
            collected.append((raw, "unknown"))

    return collected


def _extract_image_list_from(data: Any) -> list[dict[str, Any]]:
    """Return a list of image-ish dicts if `data` appears to contain one."""
    if isinstance(data, list):
        return [
            x for x in data if isinstance(x, dict) and isinstance(x.get("url"), str)
        ]
    if isinstance(data, dict):
        for key in ("items", "images", "assets", "media"):
            sub = data.get(key)
            if isinstance(sub, list):
                return [
                    x
                    for x in sub
                    if isinstance(x, dict) and isinstance(x.get("url"), str)
                ]
    return []


def _sanitize_gallery(
    raw_items: list[tuple[dict[str, Any], ImageRole]],
) -> tuple[list[GalleryItem], int, int, bool]:
    accepted: list[GalleryItem] = []
    rejected = 0
    seen: set[str] = set()
    for raw, default_role in raw_items:
        item = _sanitize_gallery_item(raw, default_role)
        if item is None:
            rejected += 1
            continue
        if item.url in seen:
            continue
        seen.add(item.url)
        accepted.append(item)
    truncated = len(accepted) > MAX_GALLERY_ITEMS
    if truncated:
        accepted = accepted[:MAX_GALLERY_ITEMS]
    return accepted, len(raw_items), rejected, truncated


def _flatten_brief(
    brief_data: dict[str, Any] | None,
) -> tuple[FlatBrief | None, list[Warning]]:
    if not isinstance(brief_data, dict):
        return None, []

    warnings: list[Warning] = []

    # `brief_data` is `action_execution_gates.brief.response.data` — the account+brief object
    profile = brief_data.get("profile") or {}
    brief_obj = _nested_brief_dict(brief_data)
    form_values = brief_obj.get("form_values") or {}

    # Web golden shape sometimes places fields at the top level of `data`
    # (e.g., name, description, services, reviews, location, ...).
    # We treat `brief_data` itself as an additional source for fallback lookups.
    top = brief_data

    business_name = _first_non_empty(
        form_values.get("FIELD_COMPANY_NAME"),
        profile.get("business_name"),
        brief_obj.get("name"),
        top.get("name"),
    )
    category = _first_non_empty(
        form_values.get("FIELD_COMPANY_CATEGORY"),
        top.get("industry"),
        top.get("business_type"),
        profile.get("category"),
    )
    country = _first_non_empty(
        form_values.get("FIELD_COUNTRY"),
        top.get("country"),
    )
    business_description = _first_non_empty(
        form_values.get("FIELD_LARGE_ANSWER"),
        top.get("description"),
    )
    target_customer = _first_non_empty(
        form_values.get("FIELD_TARGET_CUSTOMER_ANSWER"),
        top.get("target_customer"),
    )
    value_proposition = _first_non_empty(
        form_values.get("FIELD_VALUE_PROPOSITION"),
        top.get("value_proposition"),
    )
    # profile.tone can be a dict {"tone": ["tone_friendly"]} or a plain string
    tone_raw = profile.get("tone")
    if isinstance(tone_raw, dict):
        _tone_list = tone_raw.get("tone") or []
        profile_tone: str | None = (
            ", ".join(t.replace("tone_", "") for t in _tone_list if isinstance(t, str))
            or None
        )
    else:
        profile_tone = _clean_string(tone_raw)

    # FIELD_COMMUNICATION_STYLE can be a list ["friendly", "informal"] or a plain string
    comm_style_raw = form_values.get("FIELD_COMMUNICATION_STYLE")
    if isinstance(comm_style_raw, list):
        comm_style: str | None = (
            ", ".join(s for s in comm_style_raw if isinstance(s, str)) or None
        )
    else:
        comm_style = _clean_string(comm_style_raw)

    tone = _first_non_empty(
        profile_tone,
        comm_style,
        top.get("brand_voice"),
    )
    communication_language = (
        _first_non_empty(
            form_values.get("FIELD_COMMUNICATION_LANGUAGE"),
            top.get("communication_language"),
        )
        or "spanish"
    )

    colors: list[str] = []
    for source in (
        form_values.get("FIELD_COLOR_LIST_PICKER") or [],
        [top.get("brand_primary_color")],
        [top.get("brand_accent_color")],
    ):
        for c in source:
            c_clean = _clean_string(c)
            if c_clean and c_clean not in colors:
                colors.append(c_clean)

    keywords: list[str] = []
    for source in (
        form_values.get("FIELD_KEYWORDS_TAGS_INPUT") or [],
        brief_obj.get("keywords") or [],
    ):
        for k in source:
            k_clean = _clean_string(k)
            if k_clean and k_clean not in keywords:
                keywords.append(k_clean)

    website_url = _first_non_empty(
        form_values.get("FIELD_WEBSITE_URL"),
        profile.get("website_url"),
        top.get("website_current"),
    )

    has_brand_material = bool(
        form_values.get("FIELD_HAS_BRAND_MATERIAL")
        or form_values.get("FIELD_BRAND_MATERIAL")
        or top.get("photos_urls")
    )

    # Freeform onboarding wish
    brief_background = _first_non_empty(
        brief_obj.get("brief"),
        top.get("brief"),
    )

    # Extras: every unknown key from `top` and from `form_values` that we did not consume above.
    consumed_top_keys = {
        "name",
        "industry",
        "business_type",
        "country",
        "description",
        "target_customer",
        "value_proposition",
        "brand_voice",
        "communication_language",
        "brand_primary_color",
        "brand_accent_color",
        "brief",
        "website_current",
        "photos_urls",
        "profile",
        "uuid",
        "business_cid",
        # these live in brief_obj and are separate
    }
    extras: dict[str, Any] = {}
    for key, value in top.items():
        if key in consumed_top_keys or key == "brief":
            continue
        extras[key] = value

    # Merge brief_obj scalars/collections that may be useful (tone_preferences, site_type, etc.)
    for key, value in brief_obj.items():
        if key in ("form_values", "keywords", "brief"):
            continue
        if key not in extras:
            extras[key] = value

    # Merge only a safe subset of form_values extras to keep prompt context compact.
    consumed_form_keys = {
        "FIELD_COMPANY_NAME",
        "FIELD_COMPANY_CATEGORY",
        "FIELD_COUNTRY",
        "FIELD_LARGE_ANSWER",
        "FIELD_TARGET_CUSTOMER_ANSWER",
        "FIELD_VALUE_PROPOSITION",
        "FIELD_COMMUNICATION_STYLE",
        "FIELD_COMMUNICATION_LANGUAGE",
        "FIELD_COLOR_LIST_PICKER",
        "FIELD_KEYWORDS_TAGS_INPUT",
        "FIELD_HAS_BRAND_MATERIAL",
        "FIELD_BRAND_MATERIAL",
        "FIELD_WEBSITE_URL",
    }
    form_extras = {
        k: v
        for k, v in form_values.items()
        if k not in consumed_form_keys and k in ALLOWED_FORM_EXTRAS
    }
    if form_extras:
        extras["form_values"] = form_extras

    if value_proposition is None:
        warnings.append(
            Warning(
                code="value_proposition_empty",
                message="Brief has no value proposition",
                field="value_proposition",
            )
        )
    if tone is None:
        warnings.append(
            Warning(
                code="tone_unclear", message="No tone signal in brief", field="tone"
            )
        )

    flat = FlatBrief(
        business_name=business_name,
        category=category,
        country=country,
        business_description=business_description,
        target_customer=target_customer,
        value_proposition=value_proposition,
        tone=tone,
        communication_language=communication_language,
        colors=colors,
        keywords=keywords,
        website_url=website_url,
        has_brand_material=has_brand_material,
        brief_background=brief_background,
        extras=extras,
    )
    return flat, warnings


_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_HEX_EXTRACT_RE = re.compile(r"(#[0-9a-fA-F]{3,8})\b")


def _extract_hex(value: str) -> str | None:
    """Return the first hex color found in value, e.g. 'primary:#E31A1A' -> '#e31a1a'."""
    m = _HEX_EXTRACT_RE.search(value)
    return m.group(1).lower() if m else None


def _parse_keywords(raw: Any) -> list[str]:
    """Accept a list or a JSON-encoded string of keywords."""
    if isinstance(raw, list):
        return [k.strip() for k in raw if isinstance(k, str) and k.strip()]
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                import json as _json

                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    return [
                        k.strip() for k in parsed if isinstance(k, str) and k.strip()
                    ]
            except Exception:
                pass
        return [k.strip() for k in stripped.split(",") if k.strip()]
    return []


_URL_RE = re.compile(r"https?://[^\s<>'\"]+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s\-().]{6,}\d(?!\w)")
_PRICE_RE = re.compile(r"\d+[\d.,]*\s?(?:€|EUR|eur|euros|usd|USD|\$)")

_SURFACE_KEYWORDS: tuple[tuple[SurfaceFormat, tuple[str, ...]], ...] = (
    ("story", ("story", "stories", "historia", "historias", "ig story", "ig stories")),
    ("reel", ("reel", "reels", "vídeo corto", "video corto")),
    ("carousel", ("carrusel", "carrousel", "carousel")),
    ("post", ("post simple", "single post", "feed post")),
)


def _detect_requested_surface(user_request: str) -> SurfaceFormat | None:
    text = user_request.lower()
    for surface, keywords in _SURFACE_KEYWORDS:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text):
                return surface
    return None


def _extract_brand_tokens(form_values: dict[str, Any], tone: str | None) -> BrandTokens:
    palette_raw = form_values.get("FIELD_COLOR_LIST_PICKER") or []
    palette: list[str] = []
    for c in palette_raw:
        if isinstance(c, str):
            cleaned = c.strip().lower()
            if _HEX_RE.fullmatch(cleaned):
                palette.append(cleaned)
    comm_raw = form_values.get("FIELD_COMMUNICATION_STYLE")
    if isinstance(comm_raw, list):
        comm_str: str | None = (
            ", ".join(s for s in comm_raw if isinstance(s, str)) or None
        )
    else:
        comm_str = _clean_string(comm_raw)
    return BrandTokens(
        palette=palette,
        font_style=_clean_string(form_values.get("FIELD_FONT_STYLE")),
        design_style=_clean_string(form_values.get("FIELD_DESIGN_STYLE")),
        post_content_style=_clean_string(form_values.get("FIELD_POST_CONTENT_STYLE")),
        communication_style=comm_str or tone,
        voice_from=_clean_string(form_values.get("FIELD_FROM")),
        voice_to=_clean_string(form_values.get("FIELD_TO")),
    )


def _extract_available_channels(
    flat: FlatBrief, form_values: dict[str, Any], top: dict[str, Any]
) -> list[AvailableChannel]:
    channels: list[AvailableChannel] = []
    seen: set[tuple[ChannelKind, str | None]] = set()

    def add(channel: ChannelKind, value: str | None, label: str | None = None) -> None:
        v = _clean_string(value) if value else None
        key = (channel, v)
        if key in seen:
            return
        seen.add(key)
        channels.append(
            AvailableChannel(channel=channel, url_or_handle=v, label_hint=label)
        )

    if flat.website_url:
        add("website", flat.website_url, "Visita la web")

    profile = top.get("profile") or {}

    add(
        "instagram_profile",
        form_values.get("FIELD_INSTAGRAM_URL") or profile.get("instagram"),
        "Síguenos",
    )
    add("facebook", form_values.get("FIELD_FACEBOOK_URL") or profile.get("facebook"))
    add("tiktok", form_values.get("FIELD_TIKTOK_URL"))
    add("linkedin", form_values.get("FIELD_LINKEDIN_URL"))
    add(
        "phone",
        form_values.get("FIELD_BUSINESS_PHONE") or profile.get("contact_phone"),
        "Llámanos",
    )
    add("email", form_values.get("FIELD_BUSINESS_EMAIL") or profile.get("email"))

    # Always-available channels for Instagram-style placement
    add("dm", None, "Escríbenos por DM")
    add("link_sticker", None, "Toca el sticker")

    return [
        c for c in channels if c.channel in ("dm", "link_sticker") or c.url_or_handle
    ]


def _extract_brief_facts(
    flat: FlatBrief, channels: list[AvailableChannel], form_values: dict[str, Any]
) -> BriefFacts:
    text_blob = " \n ".join(
        s
        for s in [
            flat.business_description,
            flat.value_proposition,
            flat.target_customer,
            flat.brief_background,
            form_values.get("FIELD_PRODUCTS_SERVICES_ANSWER"),
            form_values.get("FIELD_RELEVANT_DATES_ANSWER"),
        ]
        if isinstance(s, str)
    )

    urls: list[str] = []
    for c in channels:
        if c.url_or_handle and c.url_or_handle.startswith("http"):
            urls.append(c.url_or_handle.lower())
    for u in _URL_RE.findall(text_blob):
        u_clean = u.rstrip(".,;:)").lower()
        if u_clean not in urls:
            urls.append(u_clean)

    phones: list[str] = []
    for c in channels:
        if c.channel == "phone" and c.url_or_handle:
            phones.append(_normalize_phone(c.url_or_handle))
    for p in _PHONE_RE.findall(text_blob):
        n = _normalize_phone(p)
        if n and n not in phones:
            phones.append(n)

    emails: list[str] = []
    for c in channels:
        if c.channel == "email" and c.url_or_handle:
            emails.append(c.url_or_handle.lower())
    for e in _EMAIL_RE.findall(text_blob):
        if e.lower() not in emails:
            emails.append(e.lower())

    prices = list({m.strip() for m in _PRICE_RE.findall(text_blob)})

    hex_colors = list(flat.colors)

    return BriefFacts(
        urls=urls,
        phones=phones,
        emails=emails,
        prices=prices,
        hex_colors=[c.lower() for c in hex_colors],
    )


def _normalize_phone(raw: str) -> str:
    return re.sub(r"[\s\-().]", "", raw)


def _detect_prior_post(
    payload: dict[str, Any], prior_step_outputs: dict[str, dict[str, Any]]
) -> PriorPost | None:
    candidates: list[Any] = []
    client_request = payload.get("client_request") or {}
    ctx = client_request.get("context") or payload.get("context") or {}
    candidates.append(ctx.get("prior_post"))
    candidates.append((payload.get("extras") or {}).get("prior_post"))
    for step_data in prior_step_outputs.values():
        candidates.append(step_data.get("prior_post"))
        candidates.append(step_data.get("post"))
    for c in candidates:
        if isinstance(c, dict) and (c.get("caption") or c.get("image_url")):
            return PriorPost(
                caption=_clean_string(c.get("caption")),
                image_url=_clean_string(c.get("image_url")),
                posted_at=_clean_string(c.get("posted_at")),
                surface_format=c.get("surface_format")
                if c.get("surface_format") in ("post", "story", "reel", "carousel")
                else None,
            )
    return None


def _reconcile_request(live: str, background: str | None) -> bool:
    """Return True iff live and background diverge substantially."""
    if not background:
        return False
    live_tokens = {t.lower() for t in live.split() if len(t) > 3}
    bg_tokens = {t.lower() for t in background.split() if len(t) > 3}
    if not live_tokens or not bg_tokens:
        return False
    overlap = len(live_tokens & bg_tokens) / max(len(live_tokens), 1)
    # Also warn only if they are both "real" requests (>15 chars) so trivial overlap doesn't spam
    if len(live) > 15 and len(background) > 15:
        return overlap < 0.25
    return False


def _apply_user_profile(
    flat_brief: FlatBrief | None,
    brand_tokens: BrandTokens,
    available_channels: list[AvailableChannel],
    user_profile: UserProfile,
) -> tuple[FlatBrief, BrandTokens, list[AvailableChannel]]:
    """Apply UP overrides to FlatBrief, BrandTokens, and AvailableChannels.

    UP wins over brief gate on every non-empty field (§3.1–3.3).
    If flat_brief is None (brief gate absent), a minimal FlatBrief is built
    from UP data alone.
    """
    if flat_brief is None:
        flat_brief = FlatBrief()

    identity = user_profile.identity
    if identity is None:
        return flat_brief, brand_tokens, available_channels

    company = identity.company
    brand = identity.brand
    social = identity.social_media

    # §3.1 — FlatBrief field overrides
    if _clean_string(company.get("name")):
        flat_brief.business_name = _clean_string(company.get("name"))
    if _clean_string(company.get("category")):
        flat_brief.category = _clean_string(company.get("category"))
    if _clean_string(company.get("country")):
        flat_brief.country = _clean_string(company.get("country"))
    if _clean_string(company.get("historyAndFounder")):
        flat_brief.business_description = _clean_string(
            company.get("historyAndFounder")
        )
    if _clean_string(company.get("targetCustomer")):
        flat_brief.target_customer = _clean_string(company.get("targetCustomer"))
    if _clean_string(company.get("websiteUrl")):
        flat_brief.website_url = _clean_string(company.get("websiteUrl"))
    if _clean_string(brand.get("communicationStyle")):
        flat_brief.tone = _clean_string(brand.get("communicationStyle"))
    if _clean_string(brand.get("communicationLang")):
        flat_brief.communication_language = _clean_string(
            brand.get("communicationLang")
        )
    up_colors: list[str] = [
        c
        for c in (
            _extract_hex(x) for x in (brand.get("colors") or []) if isinstance(x, str)
        )
        if c is not None
    ]
    if up_colors:
        flat_brief.colors = up_colors
    kw_raw = brand.get("keywords")
    if kw_raw:
        up_keywords = _parse_keywords(kw_raw)
        if up_keywords:
            flat_brief.keywords = up_keywords
    if brand.get("hasMaterial") is not None:
        flat_brief.has_brand_material = bool(brand.get("hasMaterial"))

    # UP-only fields → extras
    for up_val, extras_key in [
        (company.get("subcategory"), "subcategory"),
        (company.get("productServices"), "product_services"),
        (company.get("storeType"), "store_type"),
        (company.get("location"), "location"),
        (brand.get("logoUrl"), "logo_url"),
    ]:
        val = _clean_string(up_val)
        if val:
            flat_brief.extras[extras_key] = val

    # §3.2 — BrandTokens overrides
    up_palette: list[str] = []
    for c in brand.get("colors") or []:
        if isinstance(c, str):
            hex_val = _extract_hex(c)
            if hex_val:
                up_palette.append(hex_val)
    brand_tokens.palette = up_palette if up_palette else brand_tokens.palette
    if _clean_string(brand.get("font")):
        brand_tokens.font_style = _clean_string(brand.get("font"))
    if _clean_string(brand.get("designStyle")):
        brand_tokens.design_style = _clean_string(brand.get("designStyle"))
    if _clean_string(brand.get("postContentStyle")):
        brand_tokens.post_content_style = _clean_string(brand.get("postContentStyle"))
    if _clean_string(brand.get("communicationStyle")):
        brand_tokens.communication_style = _clean_string(
            brand.get("communicationStyle")
        )

    # §3.3 — AvailableChannels overrides
    up_channel_map: dict[str, str | None] = {
        "website": _clean_string(company.get("websiteUrl")),
        "instagram_profile": _clean_string(social.get("instagramUrl")),
        "facebook": _clean_string(social.get("facebookUrl")),
        "tiktok": _clean_string(social.get("tiktokUrl")),
        "linkedin": _clean_string(social.get("linkedinUrl")),
        "phone": _clean_string(company.get("businessPhone")),
        "email": _clean_string(company.get("email")),
    }
    merged: list[AvailableChannel] = []
    seen_kinds: set[str] = set()
    for ch in available_channels:
        up_val_ch = up_channel_map.get(ch.channel)
        merged.append(
            AvailableChannel(
                channel=ch.channel,
                url_or_handle=up_val_ch if up_val_ch else ch.url_or_handle,
                label_hint=ch.label_hint,
            )
        )
        seen_kinds.add(ch.channel)
    for kind, up_url in up_channel_map.items():
        if up_url and kind not in seen_kinds:
            merged.append(
                AvailableChannel(channel=kind, url_or_handle=up_url)  # type: ignore[arg-type]
            )
            seen_kinds.add(kind)
    for always in ("dm", "link_sticker"):
        if always not in seen_kinds:
            hints = {"dm": "Escríbenos por DM", "link_sticker": "Toca el sticker"}
            merged.append(
                AvailableChannel(channel=always, label_hint=hints[always])  # type: ignore[arg-type]
            )
    available_channels = [
        c for c in merged if c.channel in ("dm", "link_sticker") or c.url_or_handle
    ]

    return flat_brief, brand_tokens, available_channels


MAX_QUANTITY_PER_JOB = 10


def _extract_subscription_jobs(
    client_request: dict[str, Any],
) -> tuple[list[SubscriptionJob], list[Warning]]:
    """Parse ``client_request.jobs`` into a list of SubscriptionJob.

    Each job is expanded by its ``quantity`` field: a job with quantity=2
    produces 2 SubscriptionJob entries with sequential indices. This lets
    the LLM see N separate items in the prompt and generate N varied
    enrichments.

    Returns (jobs, warnings). Jobs with missing required fields are skipped
    with a warning.
    """
    raw_jobs = client_request.get("jobs")
    warnings: list[Warning] = []
    if not isinstance(raw_jobs, list) or not raw_jobs:
        return [], warnings

    expanded: list[SubscriptionJob] = []
    seq_index = 0
    for idx, item in enumerate(raw_jobs):
        if not isinstance(item, dict):
            warnings.append(
                Warning(
                    code="job_invalid",
                    message=f"jobs[{idx}] is not an object — skipped",
                    field=f"jobs[{idx}]",
                )
            )
            continue
        action_key = _clean_string(item.get("action_key")) or "create_prod_line"
        description = _clean_string(item.get("description"))
        if not description:
            warnings.append(
                Warning(
                    code="job_missing_description",
                    message=f"jobs[{idx}] has no description — skipped",
                    field=f"jobs[{idx}].description",
                )
            )
            continue

        # Quantity: expand into N entries
        raw_qty = item.get("quantity", 1)
        quantity = max(1, int(raw_qty) if isinstance(raw_qty, (int, float)) else 1)
        quantity = min(quantity, MAX_QUANTITY_PER_JOB)

        # Router fields
        slug = _clean_string(item.get("slug"))
        orch_agent = _clean_string(item.get("orchestrator_agent"))
        product_uuid = _clean_string(item.get("product_uuid"))

        for _ in range(quantity):
            expanded.append(
                SubscriptionJob(
                    action_key=action_key,
                    description=description,
                    index=seq_index,
                    quantity=quantity,
                    slug=slug,
                    orchestrator_agent=orch_agent,
                    product_uuid=product_uuid,
                )
            )
            seq_index += 1

    return expanded, warnings


def normalize(
    envelope_data: dict[str, Any],
    user_profile: UserProfile | None = None,
    usp_warning: str | None = None,
    gallery_pool: GalleryPool | None = None,
    gallery_warning: str | None = None,
) -> tuple[InternalContext, list[Warning]]:
    """Parse a raw ROUTER envelope dict into InternalContext + warnings.

    Raises ValueError on unsupported_action_code. Missing required fields raise
    pydantic.ValidationError via RouterEnvelope.
    """
    envelope = RouterEnvelope.model_validate(envelope_data)
    payload = envelope.payload or {}
    client_request = payload.get("client_request") or {}
    context = payload.get("context") or {}
    gates = payload.get("action_execution_gates") or {}
    agent_sequence = payload.get("agent_sequence") or {}

    user_request = _clean_string(client_request.get("description"))
    if not user_request:
        raise ValueError("client_request.description is required")

    action_code, surface, mode = _parse_action_code(envelope.action_code)

    warnings: list[Warning] = []

    # Brief extraction
    brief_gate = gates.get("brief") or {}
    brief_passed = brief_gate.get("passed") is True
    brief_data = None
    if brief_passed:
        response = brief_gate.get("response") or {}
        brief_data = response.get("data") if isinstance(response, dict) else None
    if brief_data is None:
        warnings.append(
            Warning(
                code="brief_missing", message="Brief gate did not pass or data absent"
            )
        )
    flat_brief, brief_warnings = _flatten_brief(brief_data)
    warnings.extend(brief_warnings)

    # Gallery extraction + sanitization
    raw_items = _collect_raw_images(payload)
    gallery, raw_count, rejected, truncated = _sanitize_gallery(raw_items)
    if raw_count == 0 and not gallery:
        warnings.append(Warning(code="gallery_empty", message="No images supplied"))
    elif raw_count > 0 and not gallery:
        warnings.append(
            Warning(
                code="gallery_all_filtered",
                message=f"All {raw_count} raw images rejected by sanitization",
            )
        )
    elif rejected > 0:
        warnings.append(
            Warning(
                code="gallery_partially_filtered",
                message=f"{rejected} of {raw_count} images rejected by sanitization",
            )
        )
    if truncated:
        warnings.append(
            Warning(
                code="gallery_truncated",
                message=f"Gallery truncated to {MAX_GALLERY_ITEMS} items",
            )
        )

    # Context-id check for edits
    post_id = _clean_string(context.get("post_id"))
    website_id = _clean_string(context.get("website_id") or context.get("site_url"))
    section_id = _clean_string(context.get("section_id"))
    if mode == "edit":
        if surface == "post" and not post_id:
            warnings.append(
                Warning(
                    code="context_missing_id",
                    message="edit_post without post_id",
                    field="post_id",
                )
            )
        if surface == "web" and not website_id:
            warnings.append(
                Warning(
                    code="context_missing_id",
                    message="edit_web without website_id",
                    field="website_id",
                )
            )

    # Brief/request reconciliation
    if flat_brief and _reconcile_request(user_request, flat_brief.brief_background):
        warnings.append(
            Warning(
                code="brief_request_mismatch",
                message="Brief background and live request diverge substantially; live request wins",
            )
        )

    # Request-vague heuristic
    word_count = len(user_request.split())
    if word_count < 15:
        warnings.append(
            Warning(
                code="request_vague", message=f"Live request has {word_count} words"
            )
        )

    # Prior-step outputs (usually empty)
    previous = agent_sequence.get("previous") or {}
    prior_step_outputs: dict[str, dict[str, Any]] = {}
    if isinstance(previous, dict):
        for step_code, entry in previous.items():
            if isinstance(entry, dict) and isinstance(entry.get("output_data"), dict):
                prior_step_outputs[step_code] = entry["output_data"]

    # v2 anchors: brand tokens + channels + facts (best-effort, never fails)
    brief_obj_for_tokens: dict[str, Any] = {}
    if isinstance(brief_data, dict):
        brief_obj_for_tokens = _nested_brief_dict(brief_data)
    form_values_for_tokens = brief_obj_for_tokens.get("form_values") or {}
    top_for_tokens = brief_data if isinstance(brief_data, dict) else {}

    if flat_brief is not None:
        brand_tokens = _extract_brand_tokens(form_values_for_tokens, flat_brief.tone)
        available_channels = _extract_available_channels(
            flat_brief, form_values_for_tokens, top_for_tokens
        )
        brief_facts = _extract_brief_facts(
            flat_brief, available_channels, form_values_for_tokens
        )
    else:
        brand_tokens = BrandTokens()
        available_channels = []
        brief_facts = BriefFacts()

    # USP Memory Gateway integration
    user_insights: list[dict[str, Any]] = []
    if usp_warning:
        warnings.append(
            Warning(code=usp_warning, message=f"USP: {usp_warning.replace('_', ' ')}")
        )
    if user_profile is not None:
        flat_brief, brand_tokens, available_channels = _apply_user_profile(
            flat_brief, brand_tokens, available_channels, user_profile
        )
        # Rebuild BriefFacts from the post-merge data (§3.5)
        if flat_brief is not None:
            brief_facts = _extract_brief_facts(
                flat_brief, available_channels, form_values_for_tokens
            )
        user_insights = [
            {
                "key": i.key,
                "insight": i.insight,
                "confidence": i.confidence,
                "sourceIdentifier": i.source_identifier,
                "updatedAt": i.updated_at,
            }
            for i in user_profile.insights
        ]

    # Gallery Image Pool integration (§10.2 of spec 11)
    if gallery_warning:
        warnings.append(
            Warning(
                code=gallery_warning,
                message=f"Gallery: {gallery_warning.replace('_', ' ')}",
            )
        )
    if gallery_pool is not None:
        if gallery_pool.truncated:
            warnings.append(
                Warning(
                    code="gallery_pool_truncated",
                    message="Gallery page 1 may not cover all account images (size limit reached)",
                )
            )
        if len(gallery_pool.shortlist) == 0 and not gallery_warning:
            warnings.append(
                Warning(
                    code="gallery_vision_shortlist_empty",
                    message="Gallery eligible pool produced 0 vision candidates",
                )
            )

    requested_surface_format = (
        _detect_requested_surface(user_request) if surface == "post" else None
    )

    prior_post = (
        _detect_prior_post(payload, prior_step_outputs)
        if mode == "edit" and surface == "post"
        else None
    )

    # subscription_strategy: extract per-job descriptors
    subscription_jobs: list[SubscriptionJob] | None = None
    if action_code == "subscription_strategy":
        sub_jobs, sub_warnings = _extract_subscription_jobs(client_request)
        warnings.extend(sub_warnings)
        if not sub_jobs:
            raise ValueError(
                "subscription_strategy requires at least one valid job in client_request.jobs"
            )
        subscription_jobs = sub_jobs

    ctx = InternalContext(
        task_id=envelope.task_id,
        correlation_id=envelope.correlation_id,
        callback_url=envelope.callback_url,
        action_code=action_code,
        surface=surface,
        mode=mode,
        user_request=user_request,
        attachments=_normalize_attachment_urls(client_request.get("attachments")),
        account_uuid=_clean_string(context.get("account_uuid")),
        client_name=_clean_string(context.get("client_name")),
        platform=_clean_string(context.get("platform")),
        post_id=post_id,
        website_id=website_id,
        section_id=section_id,
        brief=flat_brief,
        gallery=gallery,
        brand_tokens=brand_tokens,
        available_channels=available_channels,
        brief_facts=brief_facts,
        prior_post=prior_post,
        requested_surface_format=requested_surface_format,
        prior_step_outputs=prior_step_outputs,
        user_insights=user_insights,
        gallery_pool=gallery_pool,
        gallery_raw_count=raw_count,
        gallery_rejected_count=rejected,
        gallery_truncated=truncated,
        subscription_jobs=subscription_jobs,
        raw_envelope=envelope_data,
    )
    return ctx, warnings
