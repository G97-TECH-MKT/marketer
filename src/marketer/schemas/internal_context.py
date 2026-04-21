"""Internal normalized context that the reasoner and prompts operate on.

Flattens the messy ROUTER envelope + Spanish-keyed brief into stable, typed
fields + freeform extras (SPEC §5).

v2 additions for the post-focused iteration:
- BrandTokens (palette, font, voice signals) extracted deterministically.
- AvailableChannel list (the only allowed CTA targets).
- BriefFacts (urls, phones, emails, prices, hex colors) the validator
  cross-references to detect hallucination.
- prior_post (for edit_post; required by reasoner).
- requested_surface_format (deterministic gate from explicit request keywords).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Surface = Literal["post", "web"]
Mode = Literal["create", "edit"]
ActionCode = Literal["create_post", "edit_post", "create_web", "edit_web"]
ImageRole = Literal["brand_asset", "content", "reference", "unknown"]
SurfaceFormat = Literal["post", "story", "reel", "carousel"]
ChannelKind = Literal[
    "website",
    "instagram_profile",
    "facebook",
    "tiktok",
    "linkedin",
    "phone",
    "whatsapp",
    "email",
    "dm",
    "link_sticker",
    "none",
]


class GalleryItem(BaseModel):
    url: str
    name: str | None = None
    extension: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    category: str | None = None
    role: ImageRole = "unknown"
    used_previously: bool | None = None


class BrandTokens(BaseModel):
    """Hard brand-side anchors. The LLM must compose AROUND these, not invent."""

    palette: list[str] = Field(default_factory=list, description="Hex codes from FIELD_COLOR_LIST_PICKER, lowercased.")
    font_style: str | None = None
    design_style: str | None = None
    post_content_style: str | None = None
    communication_style: str | None = None
    voice_from: str | None = None
    voice_to: str | None = None


class AvailableChannel(BaseModel):
    """Channels the CTA is allowed to target."""

    channel: ChannelKind
    url_or_handle: str | None = None
    label_hint: str | None = None


class BriefFacts(BaseModel):
    """Tokens the LLM is allowed to mention literally. Validator cross-checks."""

    urls: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    prices: list[str] = Field(default_factory=list)
    hex_colors: list[str] = Field(default_factory=list)


class PriorPost(BaseModel):
    """Snapshot of the post being edited (required for edit_post)."""

    caption: str | None = None
    image_url: str | None = None
    posted_at: str | None = None
    surface_format: SurfaceFormat | None = None


class FlatBrief(BaseModel):
    business_name: str | None = None
    category: str | None = None
    country: str | None = None
    business_description: str | None = None
    target_customer: str | None = None
    value_proposition: str | None = None
    tone: str | None = None
    communication_language: str | None = None
    colors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    website_url: str | None = None
    has_brand_material: bool = False

    brief_background: str | None = None

    extras: dict[str, Any] = Field(default_factory=dict)


class InternalContext(BaseModel):
    task_id: str
    correlation_id: str | None = None
    callback_url: str
    action_code: ActionCode
    surface: Surface
    mode: Mode

    user_request: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    account_uuid: str | None = None
    client_name: str | None = None
    platform: str | None = None
    post_id: str | None = None
    website_id: str | None = None
    section_id: str | None = None

    brief: FlatBrief | None = None
    gallery: list[GalleryItem] = Field(default_factory=list)

    # v2 anchors / facts
    brand_tokens: BrandTokens = Field(default_factory=BrandTokens)
    available_channels: list[AvailableChannel] = Field(default_factory=list)
    brief_facts: BriefFacts = Field(default_factory=BriefFacts)
    prior_post: PriorPost | None = None
    requested_surface_format: SurfaceFormat | None = None

    prior_step_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)

    gallery_raw_count: int = 0
    gallery_rejected_count: int = 0
    gallery_truncated: bool = False

    raw_envelope: dict[str, Any] = Field(default_factory=dict)
