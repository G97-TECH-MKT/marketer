"""Enrichment payload returned by MARKETER (v2 — post-focused).

Adds compared-and-committed strategic decisions, structured caption, separated
image brief (concept / generation_prompt / alt_text), channel-aware CTA,
hashtag direction (not list), per-choice confidence, do_not guardrails for
downstream agents, and an explicit content pillar.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class Confidence(BaseModel):
    """Per-choice confidence so downstream can decide where to escalate."""

    surface_format: ConfidenceLevel = "medium"
    angle: ConfidenceLevel = "medium"
    palette_match: ConfidenceLevel = "medium"
    cta_channel: ConfidenceLevel = "medium"


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
    surface: Literal["post", "web"]
    mode: Literal["create", "edit"]
    latency_ms: int = 0
    gemini_model: str = ""
    repair_attempted: bool = False
    degraded: bool = False
    gallery_stats: GalleryStats = Field(default_factory=GalleryStats)
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0


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
