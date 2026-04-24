"""Enrichment payload returned by MARKETER (v2 — post-focused).

Adds compared-and-committed strategic decisions, structured caption, separated
image brief (concept / generation_prompt / alt_text), channel-aware CTA,
hashtag direction (not list), per-choice confidence, do_not guardrails for
downstream agents, and an explicit content pillar.
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
ConfidenceLevel = Literal["high", "medium", "low"]
HashtagIntent = Literal[
    "local_discovery",
    "brand_awareness",
    "community",
    "promotion",
    "education",
    "engagement",
    "none",
]


# ---------------------------------------------------------------------------
# LLM output coercion helpers
# Gemini sometimes emits Spanish labels, informal aliases, or capitalized
# variants for enum fields. These maps normalise before Pydantic's Literal
# check runs, avoiding unnecessary repair LLM calls.
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

_CONFIDENCE_LEVEL_ALIASES: dict[str, str] = {
    "high": "high",
    "alto": "high",
    "alta": "high",
    "medium": "medium",
    "medio": "medium",
    "media": "medium",
    "moderate": "medium",
    "moderado": "medium",
    "low": "low",
    "bajo": "low",
    "baja": "low",
}

_SELECTED_IMAGE_ROLE_ALIASES: dict[str, str] = {
    "hero": "hero",
    "main": "hero",
    "principal": "hero",
    "primary": "hero",
    "supporting": "supporting",
    "soporte": "supporting",
    "support": "supporting",
    "secondary": "supporting",
    "secundario": "supporting",
    "background": "background",
    "fondo": "background",
    "bg": "background",
    "reference_only": "reference_only",
    "reference": "reference_only",
    "referencia": "reference_only",
    "ref": "reference_only",
}

_FUNNEL_STAGE_ALIASES: dict[str, str] = {
    "awareness": "awareness",
    "conciencia": "awareness",
    "conocimiento": "awareness",
    "descubrimiento": "awareness",
    "consideration": "consideration",
    "consideracion": "consideration",
    "consideración": "consideration",
    "conversion": "conversion",
    "conversión": "conversion",
    "compra": "conversion",
    "retention": "retention",
    "retencion": "retention",
    "retención": "retention",
    "fidelizacion": "retention",
    "fidelización": "retention",
    "loyalty": "retention",
    "advocacy": "advocacy",
    "abogacia": "advocacy",
    "abogacía": "advocacy",
    "recomendacion": "advocacy",
    "recomendación": "advocacy",
    "referral": "advocacy",
}


class StrategicChoice(BaseModel):
    """A decision the agent made by comparing alternatives."""

    chosen: str
    alternatives_considered: list[str] = Field(default_factory=list)
    rationale: str


class StrategicDecisions(BaseModel):
    """Force the agent to commit and explain three load-bearing choices."""

    surface_format: StrategicChoice
    angle: StrategicChoice
    voice: StrategicChoice


class CaptionParts(BaseModel):
    """Caption split for downstream layout flexibility (feed crop, story, reel cover)."""

    hook: str = Field(
        description="First line; shows above 'more' on Instagram. Keep tight."
    )
    body: str = Field(description="Main copy. May contain line breaks and emojis.")
    cta_line: str = Field(
        description="Closing call-to-action line. Empty string if pure awareness."
    )


class ImageBrief(BaseModel):
    """Image direction: separates the concept (human-readable) from the generation prompt
    (machine-readable) and accessibility text."""

    concept: str = Field(description="One sentence: what the image conveys.")
    generation_prompt: str = Field(
        description="Concrete prompt for an image generator: subject, composition, lighting, props, style, aspect."
    )
    alt_text: str = Field(description="Accessibility alt-text describing the image.")


class CallToAction(BaseModel):
    """Channel-aware CTA. Validator rejects channels not present in available_channels."""

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


_HASHTAG_INTENT_ALIASES: dict[str, str] = {
    "awareness": "brand_awareness",
    "brand": "brand_awareness",
    "local": "local_discovery",
    "discovery": "local_discovery",
    "promo": "promotion",
    "engage": "engagement",
    "edu": "education",
    "community_building": "community",
    # Gemini sometimes confuses funnel stages with hashtag intents
    "consideration": "brand_awareness",
    "conversion": "promotion",
    "retention": "community",
    "advocacy": "community",
}

# Keyword fragments (lowercase) → intent. Checked in order; first match wins.
_HASHTAG_INTENT_KEYWORDS: list[tuple[str, str]] = [
    ("local_discovery", "local_discovery"),
    ("brand_awareness", "brand_awareness"),
    ("local", "local_discovery"),
    ("descubr", "local_discovery"),
    ("brand", "brand_awareness"),
    ("awareness", "brand_awareness"),
    ("marca", "brand_awareness"),
    ("comunidad", "community"),
    ("community", "community"),
    ("promoc", "promotion"),
    ("promo", "promotion"),
    ("venta", "promotion"),
    ("educac", "education"),
    ("educati", "education"),
    ("aprendiz", "education"),
    ("engag", "engagement"),
    ("interacci", "engagement"),
    ("participaci", "engagement"),
]

_VOLUME_WORDS: dict[str, int] = {
    "none": 0,
    "low": 5,
    "bajo": 5,
    "medium": 8,
    "medio": 8,
    "moderate": 8,
    "high": 12,
    "alto": 12,
    "very high": 15,
    "muy alto": 15,
}


def _coerce_hashtag_intent(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    normalized = v.strip().lower()
    # Exact alias match first
    if normalized in _HASHTAG_INTENT_ALIASES:
        return _HASHTAG_INTENT_ALIASES[normalized]
    # Already a valid value
    valid = {
        "local_discovery",
        "brand_awareness",
        "community",
        "promotion",
        "education",
        "engagement",
        "none",
    }
    if normalized in valid:
        return normalized
    # Keyword scan for free-form / multilingual values
    for keyword, intent in _HASHTAG_INTENT_KEYWORDS:
        if keyword in normalized:
            return intent
    # Unrecognised — let Pydantic raise so repair can fix it
    return v


def _coerce_suggested_volume(v: Any) -> Any:
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        normalized = v.strip().lower()
        if normalized in _VOLUME_WORDS:
            return _VOLUME_WORDS[normalized]
        try:
            return int(normalized)
        except ValueError:
            return 0
    return v


class HashtagStrategy(BaseModel):
    """Hashtag direction plus the actual strings that go into cf_post_brief."""

    intent: HashtagIntent
    suggested_volume: int = Field(
        default=0,
        ge=0,
        le=30,
        description=(
            "Number of hashtags to publish (0-30). This is a count, not audience "
            "or search popularity volume."
        ),
    )
    themes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Actual hashtag strings with # prefix (5-10 items). "
            "These are used verbatim in cf_post_brief. Match intent and themes."
        ),
    )

    @field_validator("intent", mode="before")
    @classmethod
    def _coerce_intent(cls, v: Any) -> Any:
        return _coerce_hashtag_intent(v)

    @field_validator("suggested_volume", mode="before")
    @classmethod
    def _coerce_volume(cls, v: Any) -> Any:
        return _coerce_suggested_volume(v)


class Confidence(BaseModel):
    """Per-choice confidence so downstream can decide where to escalate."""

    surface_format: ConfidenceLevel = "medium"
    angle: ConfidenceLevel = "medium"
    palette_match: ConfidenceLevel = "medium"
    cta_channel: ConfidenceLevel = "medium"

    @field_validator(
        "surface_format", "angle", "palette_match", "cta_channel", mode="before"
    )
    @classmethod
    def _coerce_level(cls, v: Any) -> Any:
        return _coerce(v, _CONFIDENCE_LEVEL_ALIASES)


class VisualSelection(BaseModel):
    """Which gallery items downstream executors should use / avoid."""

    recommended_asset_urls: list[str] = Field(default_factory=list)
    recommended_reference_urls: list[str] = Field(default_factory=list)
    avoid_asset_urls: list[str] = Field(default_factory=list)


SelectedImageRole = Literal["hero", "supporting", "background", "reference_only"]


class SelectedImage(BaseModel):
    """A gallery image selected by the LLM during Stage 2 vision confirmation."""

    uuid: str = Field(
        description="UUID from the gallery shortlist. Must reference an item passed to the LLM."
    )
    content_url: str = Field(
        description="Public S3 URL of the image (same as GalleryPoolItem.content_url)."
    )
    role: SelectedImageRole = Field(description="Creative role in the final post.")
    usage_note: str = Field(description="One-sentence rationale for this selection.")

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> Any:
        return _coerce(v, _SELECTED_IMAGE_ROLE_ALIASES)


FunnelStage = Literal[
    "awareness",
    "consideration",
    "conversion",
    "retention",
    "advocacy",
]


class BrandIntelligence(BaseModel):
    """Internal brand reasoning — the 'soul' of marketer.

    This section is NOT meant for end-user consumption. It is the marketer's
    internal thought layer: deeper reasoning about the business, audience and
    strategy that does NOT surface in the post copy, but informs every decision
    above AND feeds downstream specialist agents (rrss, web, atlas) so they
    inherit context instead of re-inferring it.
    """

    business_taxonomy: str = Field(
        description=(
            "Stable business-type label in snake_case, 2-4 tokens. Examples: "
            "'local_food_service', 'b2b_saas_analytics', 'b2c_ecom_fashion', "
            "'professional_health_dental', 'pro_service_legal'. Used to route "
            "category-specific conventions downstream."
        ),
    )
    funnel_stage_target: FunnelStage = Field(
        description=(
            "Which funnel stage this post primarily serves. Not the business's "
            "whole funnel — THIS post's contribution."
        ),
    )

    @field_validator("funnel_stage_target", mode="before")
    @classmethod
    def _coerce_funnel(cls, v: Any) -> Any:
        return _coerce(v, _FUNNEL_STAGE_ALIASES)

    voice_register: str = Field(
        description=(
            "Tonal register in 2-5 words, richer than friendly/professional. "
            "Examples: 'nostálgico-artesanal', 'autoritativo-didáctico', "
            "'juguetón-irreverente', 'tranquilizador-profesional'. This is the "
            "nuance your downstream copywriter needs."
        ),
    )
    emotional_beat: str = Field(
        description=(
            "Primary emotion the post is designed to trigger in the reader, "
            "one or two words. Examples: 'pertenencia', 'curiosidad', "
            "'orgullo_local', 'tranquilidad', 'urgencia_suave'."
        ),
    )
    audience_persona: str = Field(
        description=(
            "1-2 sentences: who reads this, their context, and the single "
            "strongest objection they carry. Example: 'Vecino de Ruzafa, "
            "35-55, busca comida honesta sin pagar sitio de moda; objeción: "
            "¿será caro o pretencioso?'"
        ),
    )
    unfair_advantage: str = Field(
        description=(
            "One sentence naming the thing ONLY this brand can say credibly. "
            "Not a generic benefit. Must derive from the brief, not invented."
        ),
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Short tokens for regulatory/brand-safety risks downstream must "
            "handle. Examples: 'health_disclaimer_needed', 'financial_advice', "
            "'age_restricted', 'competitive_claim', 'none'. Empty list is fine."
        ),
    )
    rhetorical_device: str = Field(
        description=(
            "The primary technique the caption uses. One of: 'contraste', "
            "'especificidad_concreta', 'analogía', 'narración_origen', "
            "'dato_sorprendente', 'testimonio', 'pregunta_retórica', "
            "'enumeración', 'ninguno'. Downstream agents use this to vary "
            "technique across a calendar."
        ),
    )


class PostEnrichment(BaseModel):
    """Strategic + concrete brief for a single Instagram post/story/reel."""

    schema_version: Literal["2.0"] = "2.0"
    surface_format: SurfaceFormat = "post"
    content_pillar: ContentPillar
    title: str = Field(description="Short memorable internal title for the piece.")
    objective: str = Field(description="One-sentence business outcome.")
    brand_dna: str = Field(
        description=(
            "Design-system reference document for Content Factory. Structured "
            "format: CLIENT DNA header, Colors (hex with role and evocative name), "
            "Design Style (JSON style_reference_analysis block), Typography, Logo "
            "rules, Contact (one compact line). 200-400 words. "
            "PUBLIC: travels to CONTENT_FACTORY as client_dna."
        ),
    )
    strategic_decisions: StrategicDecisions
    visual_style_notes: str = Field(
        description="Concrete style cues anchored to brand_tokens: palette, light, framing."
    )
    narrative_connection: str | None = Field(
        default=None,
        description="How this post ties to a series; null for standalone.",
    )
    image: ImageBrief
    caption: CaptionParts
    cta: CallToAction
    hashtag_strategy: HashtagStrategy
    do_not: list[str] = Field(
        default_factory=list,
        description="Anti-patterns for downstream agents (max 5 short items).",
    )
    selected_images: list[SelectedImage] = Field(
        default_factory=list,
        description=(
            "Gallery images selected by Stage 2 LLM vision. UUIDs reference items "
            "from the gallery shortlist. Empty list is valid — text-only enrichment."
        ),
    )
    visual_selection: VisualSelection = Field(default_factory=VisualSelection)
    confidence: Confidence = Field(default_factory=Confidence)
    brand_intelligence: BrandIntelligence = Field(
        description=(
            "Internal brand reasoning layer. NOT shown to end users; feeds "
            "downstream specialist agents so they inherit strategic context."
        ),
    )
    cf_post_brief: str = Field(
        default="",
        description=(
            "Ready-to-use post instruction for Content Factory. Format: "
            "(1) CONCEPT block: 'CONCEPT — {subject}', visual description (1 sentence), "
            "brand reasoning tied to emotional_beat (1-2 sentences), "
            "'Imagen:' with gallery file name or 'AI-generated', "
            "'Tipo:' with foto_galeria|ai_generada|captura_reels; "
            "(2) 'Caption:' block with caption.hook + caption.body + caption.cta_line "
            "assembled verbatim; (3) 'Hashtags:' block with hashtag_strategy.tags. "
            "Compose this LAST. This is the direct instruction CF designer and "
            "copywriter consume — maps to client_request_posts in CF payload."
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
        description="Selected asset URLs (maps from enrichment.visual_selection.recommended_asset_urls).",
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
