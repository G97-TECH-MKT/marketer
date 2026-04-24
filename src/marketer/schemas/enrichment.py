"""Enrichment payload returned by MARKETER (v2 slim — CF-focused output).

Reduced to ~10 fields. Only what Content Factory actually consumes plus the
two strategy-seeding fields (voice_register, audience_persona). Dead-weight
fields (strategic_decisions, confidence, do_not, image, visual_selection,
brand_intelligence, etc.) have been dropped to eliminate combinatorial
validation failures and halve output token cost.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


SurfaceFormat = Literal["post", "story", "reel", "carousel"]
ContentPillar = Literal[
    "product",
    "behind_the_scenes",
    "customer",
    "education",
    "promotion",
    "community",
]
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


# ---------------------------------------------------------------------------
# LLM output coercion helpers
# ---------------------------------------------------------------------------


def _coerce(v: Any, aliases: dict[str, str]) -> Any:
    """Lowercase-strip v and return the canonical value if found in aliases."""
    if not isinstance(v, str):
        return v
    key = v.strip().lower()
    return aliases.get(key, v)


_SURFACE_FORMAT_ALIASES: dict[str, str] = {
    "post": "post",
    "instagram_post": "post",
    "ig_post": "post",
    "publicacion": "post",
    "publicación": "post",
    "foto": "post",
    "feed": "post",
    "story": "story",
    "stories": "story",
    "historia": "story",
    "instagram_story": "story",
    "ig_story": "story",
    "reel": "reel",
    "reels": "reel",
    "video": "reel",
    "instagram_reel": "reel",
    "ig_reel": "reel",
    "carousel": "carousel",
    "carrusel": "carousel",
    "carrusel_post": "carousel",
    "album": "carousel",
    "álbum": "carousel",
    "multi_image": "carousel",
    "multi-image": "carousel",
}

_CONTENT_PILLAR_ALIASES: dict[str, str] = {
    "product": "product",
    "producto": "product",
    "productos": "product",
    "servicio": "product",
    "servicios": "product",
    "behind_the_scenes": "behind_the_scenes",
    "behind the scenes": "behind_the_scenes",
    "detras_de_escenas": "behind_the_scenes",
    "detrás de escenas": "behind_the_scenes",
    "detras de escenas": "behind_the_scenes",
    "backstage": "behind_the_scenes",
    "bts": "behind_the_scenes",
    "customer": "customer",
    "cliente": "customer",
    "clientes": "customer",
    "testimonio": "customer",
    "testimonios": "customer",
    "education": "education",
    "educacion": "education",
    "educación": "education",
    "educativo": "education",
    "tutorial": "education",
    "tips": "education",
    "consejo": "education",
    "consejos": "education",
    "promotion": "promotion",
    "promocion": "promotion",
    "promoción": "promotion",
    "oferta": "promotion",
    "descuento": "promotion",
    "venta": "promotion",
    "community": "community",
    "comunidad": "community",
    "community_building": "community",
}

_CHANNEL_KIND_ALIASES: dict[str, str] = {
    "website": "website",
    "web": "website",
    "sitio_web": "website",
    "sitio web": "website",
    "url": "website",
    "link": "website",
    "instagram_profile": "instagram_profile",
    "instagram": "instagram_profile",
    "ig": "instagram_profile",
    "perfil_instagram": "instagram_profile",
    "facebook": "facebook",
    "fb": "facebook",
    "facebook_page": "facebook",
    "tiktok": "tiktok",
    "tik_tok": "tiktok",
    "tik-tok": "tiktok",
    "tt": "tiktok",
    "linkedin": "linkedin",
    "linkedin_profile": "linkedin",
    "phone": "phone",
    "telefono": "phone",
    "teléfono": "phone",
    "llamada": "phone",
    "call": "phone",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "email": "email",
    "correo": "email",
    "mail": "email",
    "correo_electronico": "email",
    "dm": "dm",
    "direct": "dm",
    "direct_message": "dm",
    "mensaje_directo": "dm",
    "link_sticker": "link_sticker",
    "sticker": "link_sticker",
    "link_in_bio": "link_sticker",
    "none": "none",
    "ninguno": "none",
    "n/a": "none",
    "no_cta": "none",
}


class CaptionParts(BaseModel):
    """Caption split for downstream layout flexibility."""

    hook: str = Field(
        description="First line; shows above 'more' on Instagram. Keep tight."
    )
    body: str = Field(description="Main copy. May contain line breaks and emojis.")
    cta_line: str = Field(
        description="Closing call-to-action line. Empty string if pure awareness."
    )


class CallToAction(BaseModel):
    """Channel-aware CTA."""

    channel: ChannelKind
    url_or_handle: str | None = Field(
        default=None,
        description="Target URL or handle. Must be in InternalContext.available_channels for url-based channels.",
    )
    label: str = Field(
        description="Button/CTA copy in communication_language (e.g. 'Reserva tu mesa')."
    )

    @field_validator("channel", mode="before")
    @classmethod
    def _coerce_channel(cls, v: Any) -> Any:
        return _coerce(v, _CHANNEL_KIND_ALIASES)


class HashtagStrategy(BaseModel):
    """Hashtag direction plus the actual strings that go into cf_post_brief."""

    themes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Actual hashtag strings with # prefix (5-10 items). "
            "These are used verbatim in cf_post_brief."
        ),
    )


class PostEnrichment(BaseModel):
    """Slim strategic brief for a single Instagram post/story/reel/carousel."""

    surface_format: SurfaceFormat = "post"
    content_pillar: ContentPillar
    brand_dna: str = Field(
        description=(
            "Design-system reference document for Content Factory. Structured "
            "format: CLIENT DNA header, Colors, Design Style JSON block, Typography, "
            "Logo rules, Contact. 200-400 words. PUBLIC: travels to CF as client_dna."
        ),
    )
    caption: CaptionParts
    cta: CallToAction
    hashtag_strategy: HashtagStrategy
    cf_post_brief: str = Field(
        default="",
        description=(
            "Ready-to-use post instruction for Content Factory. CONCEPT block + "
            "Caption block + Hashtags block. Maps to client_request in CF payload."
        ),
    )
    selected_asset_urls: list[str] = Field(
        default_factory=list,
        description=(
            "LLM-chosen image URLs for this post. Pull from gallery_pool[].content_url "
            "and/or gallery[].url. For carousel, list slides in order. Empty list is "
            "valid when no gallery image fits."
        ),
    )
    voice_register: str = Field(
        default="",
        description=(
            "Tonal register in 2-5 words. Examples: 'nostálgico-artesanal', "
            "'autoritativo-didáctico', 'juguetón-irreverente'. "
            "Used to seed strategies.brand_intelligence."
        ),
    )
    audience_persona: str = Field(
        default="",
        description=(
            "1-2 sentences: who reads this, their context, and their strongest "
            "objection. Used to seed strategies.brand_intelligence."
        ),
    )

    @field_validator("surface_format", mode="before")
    @classmethod
    def _coerce_surface(cls, v: Any) -> Any:
        return _coerce(v, _SURFACE_FORMAT_ALIASES)

    @field_validator("content_pillar", mode="before")
    @classmethod
    def _coerce_pillar(cls, v: Any) -> Any:
        return _coerce(v, _CONTENT_PILLAR_ALIASES)


class MultiEnrichmentOutput(BaseModel):
    """LLM response for subscription_strategy: one PostEnrichment per job."""

    items: list[PostEnrichment]


class Warning(BaseModel):
    code: str
    message: str
    field: str | None = None


class GalleryStats(BaseModel):
    raw_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    truncated: bool = False


class TraceInfo(BaseModel):
    task_id: str
    action_code: str
    surface: Literal["post", "web", "other"]
    mode: Literal["create", "edit"]
    latency_ms: int = 0
    gemini_model: str = ""
    repair_attempted: bool = False
    degraded: bool = False
    gallery_stats: GalleryStats = Field(default_factory=GalleryStats)
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    # Multi-job (subscription_strategy) fields — None for single-job flows
    job_index: int | None = None
    job_action_key: str | None = None
    total_jobs: int | None = None


class CFPayload(BaseModel):
    """Ready-to-use Content Factory payload, assembled from enrichment fields."""

    total_items: int = 1
    client_dna: str = Field(description="Brand DNA (maps from enrichment.brand_dna).")
    client_request: str = Field(
        description="Post/carousel brief for CF (maps from enrichment.cf_post_brief)."
    )
    resources: list[str] = Field(
        default_factory=list,
        description="Selected asset URLs (maps from enrichment.selected_asset_urls).",
    )


class CallbackOutputData(BaseModel):
    data: CFPayload
    enrichment: PostEnrichment
    warnings: list[Warning] = Field(default_factory=list)
    trace: TraceInfo


class CallbackBody(BaseModel):
    status: Literal["IN_PROGRESS", "COMPLETED", "FAILED"]
    output_data: CallbackOutputData | None = None
    error_message: str | None = None
