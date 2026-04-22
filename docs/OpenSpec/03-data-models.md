# 03 — Data Models

**Version:** 2.0  
**Last Updated:** 2026-04-21

---

## 1. Overview

Marketer uses **Pydantic v2** for all schema definitions. Models are organized in three layers:

1. **Input layer** — `RouterEnvelope` (lenient, `extra="allow"`)
2. **Internal layer** — `InternalContext` (typed, normalized)
3. **Output layer** — `PostEnrichment v2.0` (strict, public contract)

---

## 2. Input Layer

### 2.1 RouterEnvelope

**File:** `src/marketer/schemas/envelope.py`  
**Pydantic config:** `extra="allow"` (unknown fields silently ignored)

```python
class RouterEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str                          # required, non-empty
    job_id: Optional[str] = None
    action_code: str                      # required
    callback_url: str                     # required, full HTTPS URL
    correlation_id: Optional[str] = None
    action_id: Optional[str] = None
    payload: Optional[dict] = None        # any dict; parsed downstream
```

**Consumed payload paths:**

| Path | Consumed by |
|------|-------------|
| `payload.client_request.description` | User request text |
| `payload.client_request.attachments[]` | Additional gallery images as URL strings (`list[str]`) |
| `payload.context.account_uuid` | Brand identity |
| `payload.context.client_name` | Brand name |
| `payload.context.platform` | Target social platform |
| `payload.context.post_id` | For edit_post identification |
| `payload.context.website_id` | For edit_web identification |
| `payload.context.prior_post` | Original post for edit_post |
| `payload.action_execution_gates.brief` | Brand brief gate |
| `payload.action_execution_gates.image_catalog` | Gallery gate |
| `payload.agent_sequence.current` | Step metadata |
| `payload.agent_sequence.previous` | Prior step outputs |
| `payload.images[]` | Alternative gallery source |

---

## 3. Internal Layer

### 3.1 InternalContext

**File:** `src/marketer/schemas/internal_context.py`  
Produced by `normalizer.py`; consumed by `reasoner.py` (prompt builder).

```python
class InternalContext(BaseModel):
    # Identity
    task_id: str
    action_code: str
    surface: Surface           # "post" | "web"
    mode: Mode                 # "create" | "edit"
    account_uuid: Optional[str]
    client_name: Optional[str]
    platform: Optional[str]

    # User request
    user_request: str          # cleaned description text
    attachments: list[str]     # normalized client_request attachment URLs

    # Brand data
    brief: Optional[FlatBrief]
    brand_tokens: BrandTokens
    available_channels: list[AvailableChannel]
    brief_facts: BriefFacts

    # Gallery
    gallery: list[GalleryItem]
    gallery_raw_count: int
    gallery_rejected_count: int
    gallery_truncated: bool

    # Edit context
    prior_post: Optional[PriorPost]
    post_id: Optional[str]
    website_id: Optional[str]

    # Format detection
    requested_surface_format: Optional[str]   # "post" | "story" | "reel" | "carousel"

    # Pipeline context
    prior_step_outputs: dict
```

### 3.2 FlatBrief

Normalized brand brief extracted from gate response.

```python
class FlatBrief(BaseModel):
    # Core identity
    company_name: Optional[str]
    value_proposition: Optional[str]
    tone: Optional[str]
    communication_style: Optional[str]

    # Visual identity
    color_list: Optional[list[str]]        # hex codes
    font_style: Optional[str]
    design_style: Optional[str]
    post_content_style: Optional[str]

    # Contact info
    website_url: Optional[str]
    instagram_url: Optional[str]
    facebook_url: Optional[str]
    tiktok_url: Optional[str]
    linkedin_url: Optional[str]
    phone_number: Optional[str]
    email: Optional[str]
    whatsapp_number: Optional[str]

    # Market context
    target_audience: Optional[str]
    product_service_description: Optional[str]
    competitive_advantage: Optional[str]
    geographic_focus: Optional[str]
    language: Optional[str]

    # Content preferences
    content_topics: Optional[list[str]]
    posting_frequency: Optional[str]
    brand_values: Optional[list[str]]

    extras: dict                           # all unrecognized fields
```

### 3.3 BrandTokens

Hard constraints passed to LLM as anchors.

```python
class BrandTokens(BaseModel):
    palette: list[str]              # hex codes (e.g., ["#E8D5B7", "#2C1810"])
    font_style: Optional[str]
    design_style: Optional[str]
    post_content_style: Optional[str]
    communication_style: Optional[str]
```

### 3.4 AvailableChannel

One entry per reachable CTA destination.

```python
class AvailableChannel(BaseModel):
    channel: ChannelKind            # see enum below
    url_or_handle: Optional[str]    # URL, handle, phone, email, or None
    label: Optional[str]            # human-readable label
```

`ChannelKind` enum: `website | instagram_profile | facebook | tiktok | linkedin | phone | whatsapp | email | dm | link_sticker | none`

### 3.5 BriefFacts

Verified ground-truth values used by validator as anti-hallucination anchors.

```python
class BriefFacts(BaseModel):
    urls: set[str]           # all valid http(s):// URLs in brief
    phones: set[str]         # normalized phone numbers
    emails: set[str]         # email addresses
    prices: set[str]         # price strings (e.g., "19.99", "20")
    hex_colors: set[str]     # verified hex codes (normalized uppercase)
```

### 3.6 GalleryItem

One image from any source (catalog, attachment, brand material).

```python
class GalleryItem(BaseModel):
    url: str
    role: str                # "content" | "brand_asset" | "reference"
    tags: list[str]
    width: Optional[int]
    height: Optional[int]
    size_bytes: Optional[int]
    source: str              # "image_catalog" | "attachment" | "brand_material" | "images"
```

**Gallery sanitization rules:**
- URL must be `http://` or `https://`
- Extension must be in `{jpg, jpeg, png, webp}` (case-insensitive)
- `size_bytes` must be `> 0` and `< 20,971,520` (20 MB)
- Deduplication by URL
- Hard cap: 20 items (excess truncated)

### 3.7 PriorPost

Context for `edit_post` mode.

```python
class PriorPost(BaseModel):
    caption: Optional[str]
    image_url: Optional[str]
```

---

## 4. Output Layer

### 4.1 PostEnrichment v2.0

**File:** `src/marketer/schemas/enrichment.py`  
**Schema version literal:** `"2.0"`

This is the public contract between Marketer and CONTENT_FACTORY (via ROUTER).

```python
class PostEnrichment(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    surface_format: SurfaceFormat
    content_pillar: ContentPillar
    title: str
    objective: str
    brand_dna: str
    strategic_decisions: StrategicDecisions
    visual_style_notes: str
    narrative_connection: Optional[str]
    image: ImageBrief
    caption: CaptionParts
    cta: CallToAction
    hashtag_strategy: HashtagStrategy
    do_not: list[str]
    visual_selection: VisualSelection
    confidence: Confidence
    brand_intelligence: BrandIntelligence
    cf_post_brief: str
```

#### Field Descriptions

| Field | Type | Audience | Description |
|-------|------|----------|-------------|
| `schema_version` | `"2.0"` | All | Schema version marker |
| `surface_format` | SurfaceFormat | CF | Final format: post/story/reel/carousel |
| `content_pillar` | ContentPillar | CF | Content category |
| `title` | str | Internal | Short internal title (not published) |
| `objective` | str | CF | One-sentence business outcome |
| `brand_dna` | str | **Public** | Design system ref 200–400 words for CF |
| `strategic_decisions` | StrategicDecisions | Internal | Three decisions with rationale |
| `visual_style_notes` | str | CF | Palette/lighting/framing cues |
| `narrative_connection` | str | null | CF | Series name; null = standalone |
| `image` | ImageBrief | CF | Image brief with generation prompt |
| `caption` | CaptionParts | CF | Publishable caption parts |
| `cta` | CallToAction | CF | CTA button/link config |
| `hashtag_strategy` | HashtagStrategy | CF | Hashtag plan with actual tags |
| `do_not` | list[str] | CF | Up to 5 anti-patterns |
| `visual_selection` | VisualSelection | CF | Asset URL recommendations |
| `confidence` | Confidence | Internal | Per-decision confidence levels |
| `brand_intelligence` | BrandIntelligence | **Internal** | Strategic fields for subagents |
| `cf_post_brief` | str | **Public** | Ready-to-execute CF instruction |

### 4.2 SurfaceFormat

```python
class SurfaceFormat(str, Enum):
    POST = "post"
    STORY = "story"
    REEL = "reel"
    CAROUSEL = "carousel"
```

### 4.3 ContentPillar

```python
class ContentPillar(str, Enum):
    PRODUCT = "product"
    BEHIND_THE_SCENES = "behind_the_scenes"
    CUSTOMER = "customer"
    EDUCATION = "education"
    PROMOTION = "promotion"
    COMMUNITY = "community"
```

### 4.4 StrategicDecisions

Three key decisions, each with alternatives and rationale.

```python
class StrategicDecision(BaseModel):
    chosen: str
    alternatives_considered: list[str]   # 1-3 alternatives
    rationale: str                       # why this was chosen

class StrategicDecisions(BaseModel):
    surface_format: StrategicDecision
    angle: StrategicDecision
    voice: StrategicDecision
```

### 4.5 ImageBrief

```python
class ImageBrief(BaseModel):
    concept: str             # one sentence: what the image should convey
    generation_prompt: str   # concrete generator input (subject, lighting, props, style)
    alt_text: str            # accessibility description
```

### 4.6 CaptionParts

```python
class CaptionParts(BaseModel):
    hook: str      # first line (tight, sensory, ≤125 chars for post)
    body: str      # main paragraphs (emojis/line breaks allowed)
    cta_line: str  # closing CTA (empty for awareness-only)
```

**Caption length caps by surface:**

| Surface | hook | body | cta_line | total |
|---------|------|------|----------|-------|
| `post` | 125 | 1900 | 180 | 2200 |
| `story` | 80 | 220 | 80 | 250 |
| `reel` | 100 | — | — | 1000 |
| `carousel` | 125 | 1900 | 180 | 2200 |

### 4.7 CallToAction

```python
class CallToAction(BaseModel):
    channel: ChannelKind
    url_or_handle: Optional[str]   # null for dm/link_sticker/none
    label: str                     # button copy in brief language
```

**Channel-URL rules:**

| Channel | url_or_handle |
|---------|--------------|
| `website` | full URL (must be http/https) |
| `instagram_profile` | handle or full URL |
| `facebook` | full URL |
| `tiktok` | handle or full URL |
| `linkedin` | full URL |
| `phone` | phone number string |
| `whatsapp` | phone number string |
| `email` | email address |
| `dm` | null |
| `link_sticker` | null |
| `none` | null |

### 4.8 HashtagStrategy

```python
class HashtagIntent(str, Enum):
    LOCAL_DISCOVERY = "local_discovery"
    BRAND_AWARENESS = "brand_awareness"
    COMMUNITY = "community"
    PROMOTION = "promotion"
    EDUCATION = "education"
    ENGAGEMENT = "engagement"
    NONE = "none"

class HashtagStrategy(BaseModel):
    intent: HashtagIntent
    suggested_volume: int            # 0–30
    themes: list[str]                # conceptual themes (no # prefix)
    tags: list[str]                  # actual hashtags with # prefix (5–10 items)
```

### 4.9 VisualSelection

URL lists from the gallery. All URLs must be validated against gallery inputs.

```python
class VisualSelection(BaseModel):
    recommended_asset_urls: list[str]    # use these as primary assets
    recommended_reference_urls: list[str] # use these as style references
    avoid_asset_urls: list[str]          # do not use these
```

**Validator rules:**
- `recommended_asset_urls` must be a subset of accepted gallery URLs with `role != "reference"`
- URLs with `role == "reference"` are moved from assets to references automatically
- Any URL not in gallery is removed (hallucination prevention), warning `visual_hallucinated`

### 4.10 Confidence

```python
class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Confidence(BaseModel):
    surface_format: ConfidenceLevel
    angle: ConfidenceLevel
    palette_match: ConfidenceLevel
    cta_channel: ConfidenceLevel
```

### 4.11 BrandIntelligence

Internal-only fields for subagent consumption. Not passed to CONTENT_FACTORY directly.

```python
class BrandIntelligence(BaseModel):
    business_taxonomy: str       # snake_case 2-4 tokens: "local_food_service"
    funnel_stage_target: str     # awareness | consideration | conversion | retention | advocacy
    voice_register: str          # 2-5 descriptive words
    emotional_beat: str          # 1-2 words: "orgullo_local", "curiosidad"
    audience_persona: str        # 1-2 sentences: archetype + objection
    unfair_advantage: str        # 1 sentence USP
    risk_flags: list[str]        # ["health_disclaimer_needed"] or []
    rhetorical_device: str       # "contraste", "especificidad_concreta", etc.
```

---

## 5. Callback Model

### 5.1 CallbackBody

PATCH body sent to `callback_url`.

```python
class Warning(BaseModel):
    code: str
    message: str
    field: Optional[str]

class GalleryStats(BaseModel):
    raw_count: int
    accepted_count: int
    rejected_count: int
    truncated: bool

class CallbackTrace(BaseModel):
    task_id: str
    action_code: str
    surface: str
    mode: str
    latency_ms: int
    gemini_model: str
    repair_attempted: bool
    degraded: bool
    gallery_stats: Optional[GalleryStats]

class CallbackOutputData(BaseModel):
    enrichment: Optional[PostEnrichment]
    warnings: list[Warning]
    trace: CallbackTrace

class CallbackBody(BaseModel):
    status: Literal["COMPLETED", "FAILED"]
    output_data: CallbackOutputData
    error_message: Optional[str]
```

---

## 6. Schema Evolution

### Versioning Strategy

- `PostEnrichment.schema_version` is a literal field (`"2.0"`)
- Breaking changes require a new version (`"3.0"`)
- Additive changes (new optional fields) increment minor version
- CONTENT_FACTORY consumers must check `schema_version` before parsing

### Migration History

| Version | Changes |
|---------|---------|
| v1.0 | Initial schema (deprecated) |
| v2.0 | Full rewrite: `brand_dna`, `brand_intelligence`, `strategic_decisions`, `cf_post_brief` |

### Forward Compatibility

- `RouterEnvelope` uses `extra="allow"` — safe to add ROUTER fields
- `PostEnrichment` uses strict validation — CF parsers must handle optional fields
- New `ChannelKind` values require validator update + LLM prompt update

---

## 7. Enum Quick Reference

### ChannelKind
`website | instagram_profile | facebook | tiktok | linkedin | phone | whatsapp | email | dm | link_sticker | none`

### SurfaceFormat
`post | story | reel | carousel`

### ContentPillar
`product | behind_the_scenes | customer | education | promotion | community`

### HashtagIntent
`local_discovery | brand_awareness | community | promotion | education | engagement | none`

### ConfidenceLevel
`high | medium | low`

### Surface (internal)
`post | web`

### Mode (internal)
`create | edit`
