# 11 — Gallery Image Pool (Brand Media Fetch & LLM-Guided Selection)

**Version:** 1.1  
**Last Updated:** 2026-04-22  
**Status:** Specified — Pending Implementation

---

## 1. Overview

Until now, Marketer received images through two passive channels: images embedded in the ROUTER gate response (`action_execution_gates`) and attachments sent in `client_request.attachments`. Both are **push** channels — Marketer receives whatever ROUTER decided to include.

The **Gallery Image Pool** integration adds an **active pull** channel: Marketer queries the account's branded media library directly, retrieves images the account has already prepared and categorized, and runs a **two-stage selection** before the LLM call:

- **Stage 1 — Metadata scoring (deterministic):** rank all eligible images by relevance to the current task using their structured metadata (tags, mood, subject, people, description). Select a shortlist.
- **Stage 2 — Vision confirmation (multimodal):** send only the shortlisted images to Gemini for visual inspection and final selection.

This design is deliberately efficient: the expensive multimodal call touches only the pre-screened shortlist, not the full pool.

**Three concrete problems this solves:**

1. **Images are first-class creative assets.** The LLM confirms selection visually — not by guessing from text descriptions.
2. **Usage and locking are tracked.** `used_at` and `locked_until` let Marketer rotate through the library without human coordination.
3. **Downstream services need resolved references.** CONTENT_FACTORY receives `uuid + content_url + role` — not hypothetical descriptions.

If the Gallery API is unreachable, Marketer falls back to the ROUTER-supplied gallery without failing the task.

---

## 2. Gallery API Reference

### 2.1 Endpoint

```
GET https://api-dev.orbidi.com/prod-line/space-management/accounts/{account_id}/gallery
```

`{account_id}` is `payload.context.account_uuid`.

> **Environment note:** The base host differs per environment. `api-dev.orbidi.com` is dev/staging. The production host is TBD — configure via `GALLERY_API_URL` env var, never hardcode.

Protocol: HTTP/1.1 · Method: GET · Content-Type: `application/json` (response)

### 2.2 Authentication

```
X-API-KEY: {GALLERY_API_KEY}
```

This is a **service-level key**, not per-account. One key covers all accounts in a given environment. The key value is the same `X-API-KEY` pattern used by other Orbidi internal services.

### 2.3 Query Parameters

| Parameter | Value | Notes |
|---|---|---|
| `page` | `1` | Always start at page 1. Pagination is confirmed present. |
| `size` | `50` | Fetch up to 50 items per call. Sufficient for most accounts; see §2.3.1 for large galleries. |

Marketer does **not** push `used_at` or `locked_until` filters to the API — filtering is done client-side after fetch (the API does not support server-side eligibility filtering).

#### 2.3.1 Pagination Strategy

The API uses `page` + `size` pagination. For MVP, Marketer fetches **page 1 only** with `size=50`. Rationale: after eligibility filtering and metadata scoring, 50 items is more than enough to build a quality shortlist. A single HTTP call keeps the fetch latency predictable (~200–400ms).

> **Future:** If an account has a very large gallery (hundreds of images), a future iteration may fetch multiple pages and merge. For now, page 1 is sufficient and `gallery_pool_truncated` warning covers the case where the account has more than 50 items.

### 2.4 Response Shape

The API returns a JSON array at the top level. Each element is a **GalleryItem**:

```json
{
  "uuid": "63409f75-f41e-4837-80a4-058502437b12",
  "content": "https://yasuo-prodline-media-storer.s3.us-east-1.amazonaws.com/account/…/category/inspiration/<item_uuid>.png",
  "type": "img",
  "category": "Inspiración",
  "used_at": null,
  "locked_until": null,
  "description": "Quiero que esta imagen se use para inspiracion de posts",
  "metadata": {
    "mood": "Warm, playful, cheerful, child-friendly, promotional.",
    "tags": ["product", "lifestyle", "child", "warm", "energetic", "natural", "high-quality", "logo"],
    "text": "¿Cuál fue el primer juguete que hizo\nsonreír a tu peque?\npequeplaneta.com",
    "style": "Digital promotional graphic / composite image with cutout photo elements.",
    "colors": "Dominant blue background (~60%), coral/pink accents (~20%), white text (~10%).",
    "people": "1 infant, approximately under 1 year old, light skin, blonde hair, open-mouthed smiling.",
    "objects": ["baby", "toy car", "ABC blocks", "puzzle pieces", "star shapes"],
    "quality": "High-quality, sharp, clean edges, vibrant colors.",
    "setting": "Graphic designed on a flat, bright blue background.",
    "subject": "A smiling baby crawling and playing with a toy car.",
    "lighting": "Even, studio-like illumination on the baby.",
    "composition": "Vertical poster-style layout with the baby centered in the lower half."
  }
}
```

**`metadata` is sparse.** Items may have `"metadata": {}` — fully empty. This is valid and must be handled gracefully throughout the pipeline.

### 2.5 Field Reference

| Field | Type | Nullable | Semantics |
|---|---|---|---|
| `uuid` | string (UUID) | No | Stable identifier. Referenced in `selected_images` output and in any future mark-used call. |
| `content` | string (URL) | No | Public S3 URL. Used for LLM vision (Stage 2) and downstream delivery. |
| `type` | string | No | `"img"` for images. Other types are skipped at ingestion. |
| `category` | string | No | Account owner's label — freeform, no canonical taxonomy. Used as context hint. |
| `used_at` | ISO 8601 timestamp | Yes | `null` = never used. Non-null = previously included in a piece of content. |
| `locked_until` | ISO 8601 timestamp | Yes | `null` = freely available. Non-null = reserved until this datetime. |
| `description` | string | Yes | Owner's free-text instruction on when/how to use this image. High-value scoring signal. |
| `metadata.mood` | string | Yes | Emotional atmosphere. Matched against brand tone in Stage 1 scoring. |
| `metadata.tags` | string[] | Yes | Semantic tags. **Primary scoring signal in Stage 1.** |
| `metadata.text` | string | Yes | Text visible in the image (OCR-extracted). Important for legal/compliance awareness. |
| `metadata.style` | string | Yes | Visual production style. Matched against brand design style. |
| `metadata.colors` | string | Yes | Dominant color breakdown. Complements brand palette matching. |
| `metadata.people` | string | Yes | People description — age, expression, clothing, activity. Informs person/founder-centric decisions. |
| `metadata.objects` | string[] | Yes | Objects detected. Secondary scoring signal. |
| `metadata.quality` | string | Yes | Technical quality assessment. Informational only — low-quality images are not filtered out at this stage. |
| `metadata.setting` | string | Yes | Environment / physical context. |
| `metadata.subject` | string | Yes | Primary subject narrative. Matched against user request in Stage 1. |
| `metadata.lighting` | string | Yes | Lighting conditions. Informational for Stage 2 vision context. |
| `metadata.composition` | string | Yes | Spatial layout description. Fed to LLM as context in Stage 2. |

---

## 3. Image Selection Logic — Two-Stage Pipeline

Selection happens in two discrete stages. Stage 1 is deterministic and runs synchronously inside the normalizer. Stage 2 is LLM-based and runs inside the prompt/LLM call.

```
All fetched items (up to 50)
        │
        ▼  [ELIGIBILITY FILTER — deterministic]
        │   type == "img"
        │   used_at == null
        │   locked_until == null OR < now_utc
        ▼
Eligible pool (0 – N items)
        │
        ▼  [STAGE 1 — Metadata scoring — deterministic]
        │   Score each eligible image against the task brief
        │   Sort descending by score
        │   Take top GALLERY_VISION_CANDIDATES (default 5)
        ▼
Vision shortlist (0 – 5 items)
        │
        ▼  [STAGE 2 — LLM vision — multimodal]
        │   LLM sees: inline image + metadata block per item
        │   LLM selects 0–N images, assigns role + usage_note
        ▼
selected_images[] → PostEnrichment → CONTENT_FACTORY
```

### 3.1 Eligibility Filter

An image enters the eligible pool if and only if **all** criteria pass:

| Criterion | Rule |
|---|---|
| Type | `type == "img"` |
| Not used | `used_at == null` |
| Not locked | `locked_until == null` OR `locked_until < now_utc` |

Failures are silent at the item level. Only pool-level outcomes emit warnings.

### 3.2 Stage 1 — Metadata Scoring

Metadata scoring is a **deterministic, keyword-based relevance score** computed from the task context against each eligible image. No LLM is involved. This is cheap — pure string operations.

**Scoring signals (evaluated against `task.user_request + task.brief.keywords + task.brief.tone + action_code`):**

| Signal | Metadata field | Weight | How matched |
|---|---|---|---|
| Tag overlap | `metadata.tags` | HIGH | Intersection of tag list with brief keywords + user request tokens |
| Owner description match | `description` | HIGH | Substring or keyword presence from user request |
| Subject relevance | `metadata.subject` | MEDIUM | Keyword match against user request |
| Mood / tone alignment | `metadata.mood` | MEDIUM | Keyword match against `brief.communicationStyle` or `brief.tone` |
| People presence (if person-centric brief) | `metadata.people` | MEDIUM | Non-empty `people` field gets a bonus when brief mentions a founder, team, or person |
| Category match | `category` | LOW | Text similarity to post type or pillar (informational, emotional, promotional) |
| Style alignment | `metadata.style` | LOW | Keyword match against `brief.designStyle` |

**Score = weighted sum of matched signals.** The exact scoring formula is intentionally simple (it can be refined without a spec change — this is implementation detail). What matters is the relative ranking.

**Empty metadata → score 0.** Images with `metadata: {}` are valid but score zero across all signals. They land at the bottom of the ranking and are included in the vision shortlist only if the shortlist has fewer than `GALLERY_VISION_CANDIDATES` slots filled by scored images.

### 3.3 Stage 2 — Vision Shortlist Cap

After scoring, the top `GALLERY_VISION_CANDIDATES` images (default: **5**) are passed to the LLM. This cap is the key efficiency control:

- Metadata scoring does the heavy lifting (no tokens, no latency)
- Vision confirmation adds cost and latency only for the most relevant candidates
- 5 images in a multimodal call is well within Gemini Flash token budgets

If the eligible pool has fewer than `GALLERY_VISION_CANDIDATES` images, all eligible images are included in the shortlist (no truncation needed).

### 3.4 Locking Semantics

`locked_until` expresses a **reservation** by the account owner or another agent. Marketer must not select a locked image regardless of its score. Locking is evaluated at fetch time against `now_utc`.

`used_at` expresses **historical use**. The default policy is strict: never-used images only. A future config flag (`GALLERY_ALLOW_REUSE`) may relax this.

### 3.5 What Happens When Nothing Is Available

The pipeline degrades gracefully through a fallback chain. The task **never fails** due to gallery absence.

```
[1] Gallery API returns eligible, non-locked images
        → use them (ideal path)

[2] Gallery API returns items but all are used or locked
        → fall back to ROUTER-gate images (action_execution_gates / attachments)
        → emit: gallery_pool_empty

[3] Gallery API is unreachable or errors
        → fall back to ROUTER-gate images
        → emit: gallery_api_unavailable

[4] ROUTER also sent no images (no gates, no attachments)
        → selected_images: []
        → LLM produces text-only enrichment (valid output)
        → emit: gallery_empty (existing warning code)
```

**Text-only enrichment is a valid, non-degraded output.** `selected_images: []` does not set `degraded=true`. CONTENT_FACTORY is expected to handle this case — it can generate imagery using AI or stock assets when no brand image is provided. The `degraded` flag is reserved for missing brief + missing gallery simultaneously.

---

## 4. LLM Vision Integration (Stage 2)

### 4.1 Vision Is Confirmation, Not Discovery

The LLM's role is **visual confirmation** of a metadata-scored shortlist, not open-ended discovery across all available images. This distinction is deliberate:

- Discovery happens in Stage 1 (fast, deterministic, no cost)
- Confirmation happens in Stage 2 (multimodal, bounded to ≤5 images)

The LLM is not asked "which of all 50 images should we use?" — it is asked "here are the 5 most relevant images by metadata; confirm which to use and how."

### 4.2 Prompt Shape (per shortlisted image)

Each image in the vision shortlist is presented as a structured block combining inline vision + text context:

```
[IMAGE uuid=<uuid>  score=<N>  rank=<K of M>]
<inline image: content URL>

Categoría: <category>
Instrucción del cliente: <description or "(sin instrucción)">
Metadata:
  - Mood: <mood or omitted if empty>
  - Tags: <comma-joined or omitted if empty>
  - Sujeto: <subject or omitted if empty>
  - Personas: <people or omitted if empty>
  - Composición: <composition or omitted if empty>
  - Texto visible: <text or omitted if empty>
  - Colores: <colors or omitted if empty>
```

The `score` and `rank` are included so the LLM can interpret why each image was shortlisted. For items with empty metadata, only the inline image and category/description lines appear — no placeholder text for absent fields.

### 4.3 LLM Instructions for Selection

The system prompt instructs the LLM:

- Select 0 to N images from the shortlist. Selection of zero is valid.
- For each selected image, output: `uuid`, `content_url`, `role`, `usage_note`.
- `role` must be one of: `hero`, `supporting`, `background`, `reference_only`.
- `usage_note` must explain the creative decision in one sentence (e.g., "Used as hero — founder's authentic expression matches the emotional tone of the post").
- Do not invent image URLs. Only reference UUIDs that appear in the shortlist.
- If none of the shortlisted images are a good match for the brief, return `selected_images: []`.

### 4.4 Role Semantics

| Role | Meaning | Mark used? |
|---|---|---|
| `hero` | Primary image for the post. Appears front and center. | Yes |
| `supporting` | Secondary image — used alongside the hero or in carousel slots. | Yes |
| `background` | Used as a background layer; may be overlaid with text/graphics. | Yes |
| `reference_only` | Stylistic reference only — not rendered in the final output. CF ignores for composition. | No |

`reference_only` images are not marked as used because they are not consumed in the final creative.

### 4.5 Images the LLM Cannot See

Images that were eligible but scored below the shortlist cutoff are **not sent to the LLM**. They are not referenced in the prompt at all. If the shortlist produces poor selections, the correct fix is to improve Stage 1 scoring signals — not to expand the vision shortlist.

---

## 5. Data Persistence — What We Store

### 5.1 No New Database Table

Marketer does not persist the raw gallery response or the full pool to its own database. The Gallery API is the source of truth for image catalog data; Marketer should not shadow-copy it.

**What IS persisted (via existing mechanisms):**

| What | Where | How long |
|---|---|---|
| `selected_images[]` | `output_data` in the task callback body → stored by ROUTER in its `job_step_outputs` table | Durable, indefinite |
| Pool stats (sizes, source) | CloudWatch structured log at fetch time | Log retention policy (30 days) |
| Warning codes | `output_data.warnings[]` in callback body | Durable via ROUTER |

**Why this is sufficient:**

- `selected_images` in `output_data` gives full audit trail: which images Marketer chose for which task, with role and reasoning.
- If CONTENT_FACTORY needs to mark images as used, it has the UUIDs from `selected_images` and can call the Gallery API directly.
- The full pool (all 50 fetched items) is ephemeral — it has no value outside the scope of a single task execution. Logging pool size to CloudWatch is enough for monitoring.

### 5.2 Who Marks Images as Used

**Recommendation: CONTENT_FACTORY owns the mark-used call**, not Marketer.

Rationale: Marketer selects; CONTENT_FACTORY renders. The image is "used" when it appears in a published or finalized creative — not when Marketer selected it. If CONTENT_FACTORY fails after Marketer runs, Marketer's selection would incorrectly consume the image if Marketer had already marked it.

Marketer's responsibility ends at delivering `selected_images[]` in `output_data`. CONTENT_FACTORY reads that field and calls the Gallery API mark-used endpoint after successful render.

> **Open question:** CONTENT_FACTORY team must confirm they will own this call. Until confirmed, no mark-used call is implemented in either service.

---

## 6. Fetch Lifecycle

### 6.1 When the Fetch Happens

Gallery fetch runs **after the 202 ACK, in parallel with the USP fetch**, inside the async background worker:

```
POST /tasks → 202 ACCEPTED (< 300ms)
    │
    └── Background worker (_run_and_callback):
         1a. [EXISTING] Fetch User Profile    USP GraphQL    (~300ms, async)
         1b. [NEW]      Fetch Gallery Pool    Gallery API    (~200–400ms, async)
              │  both run in parallel via asyncio.gather()
              │  each fails independently, does not cancel the other
              ▼
         2. Normalize  envelope + user_profile + gallery_pool → InternalContext
              │  Eligibility filter applied
              │  Stage 1 metadata scoring → vision shortlist (≤5 items)
              ▼
         3. Prompt     InternalContext + shortlist images → Gemini multimodal prompt
              ▼
         4. LLM Call   Stage 2 vision + structured output         (~10-14s)
              │  Returns PostEnrichment with selected_images[]
              ▼
         5. Repair     if schema failure, 1 retry
              ▼
         6. Validate   deterministic checks + corrections
              ▼
         7. Callback   PATCH callback_url
                       output_data includes selected_images[]
```

### 6.2 Parallelism with USP

```python
user_profile, gallery_raw = await asyncio.gather(
    fetch_identity(account_uuid, ...),
    fetch_gallery_raw(account_uuid, ...),
    return_exceptions=True,
)
```

A `return_exceptions=True` pattern ensures one failure does not cancel the other. Each result is checked individually before proceeding.

### 6.3 Where `account_uuid` Comes From

`payload.context.account_uuid` — same field used by USP. If absent, Gallery fetch is skipped with `gallery_api_skipped` warning.

---

## 7. Failure Modes

| Condition | Behavior | Warning emitted |
|---|---|---|
| `GALLERY_API_URL` or `GALLERY_API_KEY` not set | Skip fetch silently | `gallery_api_skipped` |
| `account_uuid` absent in envelope | Skip fetch | `gallery_api_skipped` |
| Network timeout (> `GALLERY_TIMEOUT_SECONDS`) | Fall back to ROUTER-gate gallery | `gallery_api_unavailable` |
| HTTP 5xx from Gallery API | Fall back to ROUTER-gate gallery | `gallery_api_unavailable` |
| HTTP 4xx auth failure | Fall back; alert — key may be wrong | `gallery_api_unavailable` |
| HTTP 404 (account not in gallery service) | Fall back to ROUTER-gate gallery | `gallery_api_not_found` |
| All fetched images locked or used | Fall back to ROUTER-gate gallery | `gallery_pool_empty` |
| ROUTER-gate gallery also empty | `selected_images: []` — text-only enrichment | `gallery_empty` (existing) |
| Pool > 50 items (page 1 truncation) | Pool built from page 1 only | `gallery_pool_truncated` |
| Shortlist image URL fails during vision call | Skip that image, proceed with remaining | `gallery_image_load_failed` |
| Shortlist is 0 images (all scored zero, pool empty) | `selected_images: []` — valid output | — |

**No Gallery failure ever causes `status: "FAILED"`.** The task degrades gracefully through the fallback chain in §3.5.

`degraded=true` is **not** set for any gallery-related failure in isolation. It is only set when both brief AND all gallery sources (API + ROUTER-gate) are empty.

---

## 8. Configuration

### 8.1 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GALLERY_API_URL` | No | `""` | Gallery base URL without trailing slash. E.g. `https://api-dev.orbidi.com/prod-line/space-management`. Empty = integration disabled. |
| `GALLERY_API_KEY` | Prod | `""` | Value for `X-API-KEY` header. Same key covers all accounts in the environment. Empty = integration disabled. |
| `GALLERY_TIMEOUT_SECONDS` | No | `5.0` | HTTP timeout for the gallery fetch call. Keep low — latency must not add to task p95. |
| `GALLERY_PAGE_SIZE` | No | `50` | Number of items requested per page (maps to `size` query param). |
| `GALLERY_VISION_CANDIDATES` | No | `5` | Max images passed to LLM vision after Stage 1 scoring. Controls multimodal cost. |

Both `GALLERY_API_URL` and `GALLERY_API_KEY` must be non-empty for the integration to activate. Either being empty silently disables it — no error.

### 8.2 Settings Class

```python
# config.py additions
gallery_api_url: str = ""
gallery_api_key: str = ""
gallery_timeout_seconds: float = 5.0
gallery_page_size: int = 50
gallery_vision_candidates: int = 5
```

### 8.3 Secrets Management

`GALLERY_API_KEY` is stored in AWS Secrets Manager alongside `GEMINI_API_KEY`, `USP_API_KEY`, and `ORCH_CALLBACK_API_KEY`. It follows the same injection pattern: secrets are loaded at container start via the ECS task role, injected as environment variables, and never logged.

See [07 — Security §3](./07-security.md) for the injection pattern.

**Per-environment key discipline:**
- Dev key covers `api-dev.orbidi.com` only. Never use a dev key in prod.
- Prod key covers `api.orbidi.com` (or equivalent). Stored in a separate Secrets Manager entry.
- Rotation: rotate without redeployment via Secrets Manager version rotation. The container reads the key at startup; a restart is required after rotation (standard ECS rolling deploy triggers this).
- The key is never echoed in logs, warnings, or error messages. Log the key's presence/absence (boolean), not its value.

---

## 9. Warning Codes

Seven new warning codes are introduced:

| Code | Meaning | Downstream action |
|---|---|---|
| `gallery_api_skipped` | Key or URL not configured, or `account_uuid` absent | Check env config; verify ROUTER sends account_uuid |
| `gallery_api_unavailable` | Network error or HTTP error; fell back to ROUTER gallery | Monitor rate; spike = Gallery service downtime |
| `gallery_api_not_found` | account_uuid not recognized by Gallery API | Expected for new accounts; Gallery may lag onboarding |
| `gallery_pool_empty` | All fetched images are used or locked | Client needs to upload new images or unlock existing ones |
| `gallery_pool_truncated` | Page 1 of 50 items did not cover all account images | Acceptable for MVP; future multi-page fetch would resolve |
| `gallery_image_load_failed` | One or more shortlist image URLs failed during LLM vision call | Check S3 bucket permissions; often transient |
| `gallery_vision_shortlist_empty` | Stage 1 scoring produced 0 candidates (eligible pool was 0) | Synonymous with pool_empty at the vision stage; informational |

These codes appear in `output_data.warnings[]` alongside existing codes (`brief_missing`, `gallery_empty`, `user_profile_unavailable`, etc.). All are non-blocking.

---

## 10. Implementation Scope

### 10.1 New Module

**`src/marketer/gallery.py`**

HTTP client, eligibility filter, and Stage 1 metadata scorer. Single public surface:

```python
@dataclass
class GalleryPoolItem:
    uuid: str
    content_url: str
    category: str
    description: str | None
    used_at: str | None
    metadata: dict        # raw; may be {}
    score: float          # Stage 1 relevance score, 0.0 if no metadata

@dataclass
class GalleryPool:
    shortlist: list[GalleryPoolItem]   # scored, capped at GALLERY_VISION_CANDIDATES
    total_fetched: int                 # raw count from API (page 1)
    total_eligible: int                # after eligibility filter
    truncated: bool                    # eligible > GALLERY_VISION_CANDIDATES
    source: Literal["gallery_api", "router_gate", "empty"]

async def fetch_gallery_pool(
    account_uuid: str,
    base_url: str,
    api_key: str,
    task_context: dict,        # user_request + brief snippets for Stage 1 scoring
    vision_candidates: int = 5,
    page_size: int = 50,
    timeout: float = 5.0,
) -> GalleryPool | None:
    """Fetch, filter, score, and shortlist. Returns None on any fetch error."""
    ...

def score_image(item: dict, task_context: dict) -> float:
    """Deterministic metadata relevance score. No LLM. Returns 0.0 for empty metadata."""
    ...
```

No dependency on LLM modules, normalizer, or reasoner. Importable standalone.

### 10.2 Modified Modules

**`src/marketer/config.py`** — add five new settings fields (§8.2).

**`src/marketer/schemas/internal_context.py`**
- Add `GalleryPoolItem`, `GalleryPool` dataclasses
- Add `gallery_pool: GalleryPool | None = None` to `InternalContext`

**`src/marketer/schemas/enrichment.py`**
- Add `SelectedImage` model with `uuid`, `content_url`, `role`, `usage_note`
- Add `selected_images: list[SelectedImage] = []` to `PostEnrichment`

**`src/marketer/normalizer.py`**
- `normalize()` gains `gallery_pool: GalleryPool | None = None`
- When `gallery_pool` is present and `shortlist` is non-empty, stored in `InternalContext.gallery_pool`
- Existing ROUTER-gate gallery path (`InternalContext.gallery`) preserved for fallback
- Emit `gallery_pool_truncated` when `gallery_pool.truncated` is `True`
- Emit `gallery_vision_shortlist_empty` when shortlist is empty after eligibility filter

**`src/marketer/llm/prompts/system.py`**
- Add gallery shortlist image section (Stage 2 prompt shape — §4.2)
- Instruct LLM on `selected_images` output: uuid reference only, role enum, usage_note
- Clarify: only reference UUIDs from the shortlist; `selected_images: []` is valid

**`src/marketer/reasoner.py`**
- `reason()` gains `gallery_pool: GalleryPool | None = None`
- Passes it through to `normalize()`
- No mark-used call in Marketer (owned by CONTENT_FACTORY — §5.2)

**`src/marketer/main.py`**
- `_run_and_callback()`: gather USP + Gallery fetches concurrently before `asyncio.to_thread(_sync_work)`
- `run_task_sync()`: sequential fetch (dev endpoint, simplicity over performance)
- Both paths pass `task_context` dict (user_request + brief keywords) into `fetch_gallery_pool` for Stage 1 scoring

### 10.3 Resolved Questions (from v1.0)

| Question | Resolution |
|---|---|
| Endpoint and auth | Confirmed: `GET https://api-dev.orbidi.com/prod-line/space-management/accounts/{id}/gallery` · `X-API-KEY` header |
| Pagination support | Confirmed: `page` + `size` params. MVP fetches page 1, size 50. |
| Server-side filtering | Not supported. Client-side eligibility filter. |
| Vision scope | Vision only on metadata-scored shortlist (≤5 images), not full pool. |
| DB persistence | No new table. `selected_images` persisted via existing `output_data` in callback. |
| Mark-used ownership | CONTENT_FACTORY owns the mark-used call after render. |

### 10.4 Remaining Open Questions

1. **Production Gallery API URL:** `api-dev.orbidi.com` is confirmed for dev. Prod host TBD — confirm before prod deploy.
2. **CONTENT_FACTORY mark-used contract:** Does CONTENT_FACTORY have a Gallery API client? If not, they need to implement one. The mark-used endpoint (path, method, body) is TBD — not yet observed.
3. **Video items:** If the API returns `type: "video"` items, they are skipped. Confirm with platform whether `type=img` can be passed as a server-side filter to avoid fetching video items entirely.

### 10.5 Not in Scope

- Writing images to the Gallery API (Marketer is read-only)
- Multi-page gallery fetch (future)
- Video or non-image media types
- Gallery browsing, search, or re-ranking after LLM selection

---

## 11. Testing

### 11.1 Unit Tests (No Network)

**Eligibility filter:**
- `test_gallery_filter_excludes_locked`: `locked_until > now_utc` → excluded
- `test_gallery_filter_excludes_used`: `used_at` non-null → excluded
- `test_gallery_filter_passes_eligible`: both null → included

**Stage 1 scoring:**
- `test_score_image_tag_overlap`: tags matching brief keywords score higher than non-matching
- `test_score_image_empty_metadata_returns_zero`: `metadata: {}` → score `0.0`
- `test_shortlist_sorted_by_score`: higher-scored images rank before lower-scored
- `test_shortlist_capped_at_vision_candidates`: eligible pool of 10 → shortlist of 5

**Integration with normalizer:**
- `test_normalize_with_gallery_pool`: `InternalContext.gallery_pool.shortlist` populated
- `test_normalize_without_gallery_pool`: existing behavior unchanged
- `test_gallery_pool_truncated_warning`: `truncated=True` → warning emitted

**PostEnrichment output:**
- `test_selected_images_uuid_references_shortlist`: LLM output UUIDs must exist in shortlist
- `test_selected_images_empty_is_valid`: `selected_images: []` passes validation

### 11.2 Smoke Tests

The existing `db_e2e_smoke.py` uses the Nubiex synthetic `account_uuid`. The Gallery API returns 404 or empty. Expected: `gallery_api_not_found` warning, task completes with `selected_images: []`, output otherwise identical to pre-integration baseline.

Add a separate smoke fixture with a real `account_uuid` (confirmed gallery items) to exercise the full path: fetch → filter → score → vision → selection.

### 11.3 Live Integration Test

```bash
GALLERY_API_URL=https://api-dev.orbidi.com/prod-line/space-management \
GALLERY_API_KEY=<key> \
MARKETER_RUN_LIVE=1 \
PYTHONPATH=src python scripts/dev/quick_run.py
```

Verify in `reports/nubiex_dashboard.html`:
- No `gallery_api_unavailable` warning
- `selected_images` non-empty for an account with eligible images
- Each `content_url` in `selected_images` matches an item from the raw gallery response
- `used_at` non-null images are absent from the shortlist

---

## 12. Architecture Diagram (Updated Pipeline)

```
POST /tasks (202 in ~300ms)
    │
    └── Background worker:
         1a. Fetch User Profile       USP GraphQL       (~300ms, async)
         1b. Fetch Gallery Raw        Gallery API        (~300ms, async)
              │  asyncio.gather() — parallel, independent failures
              ▼
         2. Normalize + Stage 1
              │  Eligibility filter: type=img, not used, not locked
              │  Metadata scoring: tags, mood, subject, description vs task brief
              │  Shortlist: top GALLERY_VISION_CANDIDATES (default 5) by score
              ▼
         3. Build Prompt
              │  System context + brand tokens + user request
              │  + gallery shortlist: [inline image + metadata block] × ≤5
              ▼
         4. LLM Call — Stage 2 vision + structured output         (~10-14s)
              │  Gemini sees ≤5 images, selects subset
              │  Returns PostEnrichment.selected_images[]
              ▼
         5. Repair     if schema failure, 1 retry
              ▼
         6. Validate   deterministic checks
              ▼
         7. Callback   PATCH callback_url
                       output_data.selected_images → ROUTER → CONTENT_FACTORY
```

**Image selection cost profile:**

| Stage | Cost | Latency | What it does |
|---|---|---|---|
| Stage 1 (metadata scoring) | ~0 (string ops) | ~1ms | Narrows 50 → 5 |
| Stage 2 (vision) | ~$0.001 per image | ~2-4s added | Confirms 5 → N selected |

**Image flow to downstream:**

```
Gallery API  →  raw items (≤50)
                     │  eligibility filter
                     ▼
             eligible pool  →  Stage 1 score  →  shortlist (≤5)
                                                       │
                                                  Stage 2 vision
                                                       │
                                              selected_images[]
                                                       │
                                           output_data in callback
                                                       │
                                              CONTENT_FACTORY
                                          (uuid + url + role + note)
```

**Updated p50 end-to-end: ~14–15s** (Gallery fetch parallel to USP, no net latency; vision adds ~2–4s on top of existing Gemini call since it is part of the same LLM invocation, not a separate call)
