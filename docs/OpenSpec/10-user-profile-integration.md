# 10 — User Profile Integration (USP Memory Gateway)

**Version:** 1.1  
**Last Updated:** 2026-04-22  
**Status:** Specified — Pending Implementation

---

## 1. Overview

Until now, Marketer's only brand data source was the **brief gate** — a pre-fetched snapshot that ROUTER embeds in every task envelope before dispatch. This snapshot is collected once at onboarding time and may lag behind the client's current state.

The **USP Memory Gateway** (User Profile service) is a live, authoritative GraphQL API that stores the client's brand identity and accumulated learning insights. It contains the same conceptual information as the brief gate but:

- Is always current (updated on every client edit, not just at dispatch time)
- Adds structured company and brand fields not present in the brief gate (subcategory, location, storeType, historyAndFounder, logoUrl)
- Exposes **insights**: learned signals about the client (content performance, audience patterns, strategic observations) that are critical for enrichment quality

**Integration goal:** Marketer calls USP Memory Gateway at the start of every task background job, fetches both the client's identity and their insights by `account_uuid`, persists the response, and uses it to enrich or override brief gate data before the LLM sees any context.

**User Profile always wins field conflicts with the brief gate.**

If USP is unreachable, Marketer falls back to the brief gate without failing the task.

---

## 2. GraphQL API

### 2.1 Endpoint

Configured via environment variable — no hardcoded default. See §7.

### 2.2 Authentication

GraphQL-level API key in HTTP header:

```
x-api-key: {USP_API_KEY}
```

Introspection is open. Authenticated resolvers return a GraphQL-level error `"Invalid or missing API key"` rather than an HTTP 401.

### 2.3 Query Design

Marketer **owns a fixed static GraphQL query** defined once in `user_profile.py`. The LLM has no role in constructing it — it is a hardcoded string that runs as-is on every task. We select exactly the fields we need at development time; to add or remove fields, edit the string in that one file. Both `identity` and `insights` are fetched in a single HTTP request using GraphQL's multi-field query:

```graphql
query FetchUserProfile($accountUuid: String!) {
  identity(accountUuid: $accountUuid) {
    uuid
    accountUuid
    brand {
      colors
      communicationLang
      communicationStyle
      designStyle
      font
      hasMaterial
      keywords
      postContentStyle
      logoUrl
    }
    company {
      name
      category
      subcategory
      country
      businessPhone
      email
      websiteUrl
      historyAndFounder
      targetCustomer
      productServices
      storeType
      location
    }
    socialMedia {
      instagramUrl
      facebookUrl
      tiktokUrl
      linkedinUrl
    }
    status {
      isCompleted
    }
    timestamps {
      updatedAt
    }
  }
  insights(accountUuid: $accountUuid) {
    uuid
    key
    insight
    active
    confidence
    sourceIdentifier
    lastUpdateSource
    updatedAt
  }
}
```

**Variables:**

```json
{ "accountUuid": "<account_uuid from payload.context>" }
```

The query can be extended to request additional fields or add filter arguments without changes to the rest of the pipeline — only `user_profile.py` needs updating.

### 2.4 Response Shape

**Success:**

```json
{
  "data": {
    "identity": {
      "uuid": "...",
      "accountUuid": "...",
      "brand": { "colors": ["#5E204D", "#9C7945"], "communicationStyle": "íntimo, elegante", ... },
      "company": { "name": "Nubiex Men's Massage", "category": "Bienestar y salud", ... },
      "socialMedia": { "instagramUrl": "https://instagram.com/nubiex", ... },
      "status": { "isCompleted": true },
      "timestamps": { "updatedAt": "2026-04-20T10:00:00Z" }
    },
    "insights": [
      {
        "uuid": "...",
        "key": "audience_peak_hours",
        "insight": "La audiencia interactúa más entre 19:00 y 21:00 los jueves",
        "active": true,
        "confidence": 85,
        "sourceIdentifier": "instagram_analytics",
        "lastUpdateSource": "analytics_agent",
        "updatedAt": "2026-04-21T08:00:00Z"
      }
    ]
  }
}
```

**Account not found:**

```json
{ "data": { "identity": null, "insights": [] } }
```

**Auth failure:**

```json
{
  "data": { "identity": null, "insights": [] },
  "errors": [{ "message": "Invalid or missing API key", "path": ["identity"] }]
}
```

### 2.5 Type Reference

**`IdentityType`**

| Object | Field | Type | Notes |
|--------|-------|------|-------|
| `brand` | `colors` | `[String!]` | Hex list e.g. `["#5E204D"]` |
| `brand` | `communicationLang` | `String` | e.g. `"spanish"` |
| `brand` | `communicationStyle` | `String` | Tone / style string |
| `brand` | `designStyle` | `String` | Visual style descriptor |
| `brand` | `font` | `String` | Font family preference |
| `brand` | `hasMaterial` | `Boolean` | Brand assets exist |
| `brand` | `keywords` | `String` | Comma-separated tags |
| `brand` | `postContentStyle` | `String` | Content tone preference |
| `brand` | `logoUrl` | `String` | Public logo URL |
| `company` | `name` | `String` | Business display name |
| `company` | `category` | `String` | Primary business category |
| `company` | `subcategory` | `String` | Secondary category (not in brief gate) |
| `company` | `country` | `String` | Operating country |
| `company` | `businessPhone` | `String` | Contact phone |
| `company` | `email` | `String` | Contact email |
| `company` | `websiteUrl` | `String` | Website URL |
| `company` | `historyAndFounder` | `String` | Business narrative / origin story |
| `company` | `targetCustomer` | `String` | Audience description |
| `company` | `productServices` | `String` | Products & services description |
| `company` | `storeType` | `String` | Physical / online / hybrid (not in brief gate) |
| `company` | `location` | `String` | Physical location (not in brief gate) |
| `socialMedia` | `instagramUrl` | `String` | Instagram URL |
| `socialMedia` | `facebookUrl` | `String` | Facebook URL |
| `socialMedia` | `tiktokUrl` | `String` | TikTok URL |
| `socialMedia` | `linkedinUrl` | `String` | LinkedIn URL |

**`InsightType`**

| Field | Type | Notes |
|-------|------|-------|
| `uuid` | `String!` | Insight ID |
| `key` | `String!` | Machine-readable category (e.g. `"audience_peak_hours"`, `"top_content_format"`) |
| `insight` | `String!` | Human-readable insight text, passed directly to the LLM |
| `active` | `Boolean!` | Only active insights are used |
| `confidence` | `Int` | 0–100; higher = more reliable signal |
| `sourceIdentifier` | `String` | System that generated this insight |
| `lastUpdateSource` | `String` | Last agent that updated it |
| `updatedAt` | `DateTime` | Recency signal |

---

## 3. Data Mapping

### 3.1 User Profile → FlatBrief

`FlatBrief` is the normalized brand identity fed to the LLM. UP fields override brief gate on every overlapping field when the UP value is non-empty:

| User Profile field | FlatBrief field | Rule |
|---|---|---|
| `company.name` | `business_name` | UP wins if non-empty |
| `company.category` | `category` | UP wins if non-empty |
| `company.country` | `country` | UP wins if non-empty |
| `company.historyAndFounder` | `business_description` | UP wins if non-empty |
| `company.targetCustomer` | `target_customer` | UP wins if non-empty |
| `company.websiteUrl` | `website_url` | UP wins if non-empty |
| `brand.communicationStyle` | `tone` | UP wins if non-empty |
| `brand.communicationLang` | `communication_language` | UP wins if non-empty |
| `brand.colors` | `colors` | UP wins if list non-empty |
| `brand.keywords` | `keywords` | Split on `,`; UP wins if non-empty |
| `brand.hasMaterial` | `has_brand_material` | UP wins if not null |

Fields only in User Profile → stored in `FlatBrief.extras`:

| User Profile field | extras key |
|---|---|
| `company.subcategory` | `subcategory` |
| `company.productServices` | `product_services` |
| `company.storeType` | `store_type` |
| `company.location` | `location` |
| `brand.logoUrl` | `logo_url` |

Fields only in brief gate (UP has no equivalent — brief gate is the sole source):

- `value_proposition`
- `brief_background` (onboarding wish text)
- `FIELD_FROM` / `FIELD_TO` (voice perspective)
- `FIELD_RELEVANT_DATES_ANSWER`

### 3.2 User Profile → BrandTokens

| User Profile field | BrandTokens field | Rule |
|---|---|---|
| `brand.colors` | `palette` | Each value hex-validated before storing; UP wins |
| `brand.font` | `font_style` | UP wins if non-empty |
| `brand.designStyle` | `design_style` | UP wins if non-empty |
| `brand.postContentStyle` | `post_content_style` | UP wins if non-empty |
| `brand.communicationStyle` | `communication_style` | UP wins if non-empty |

### 3.3 User Profile → AvailableChannels

UP overrides the URL/handle for each channel kind when the UP value is non-empty. Existing label hints are preserved.

| User Profile field | Channel kind |
|---|---|
| `company.websiteUrl` | `website` |
| `socialMedia.instagramUrl` | `instagram_profile` |
| `socialMedia.facebookUrl` | `facebook` |
| `socialMedia.tiktokUrl` | `tiktok` |
| `socialMedia.linkedinUrl` | `linkedin` |
| `company.businessPhone` | `phone` |
| `company.email` | `email` |

`dm` and `link_sticker` are always added unconditionally.

### 3.4 Insights → InternalContext

Active insights are carried in a new `user_insights` field on `InternalContext` as a list of structured dicts:

```python
user_insights: list[dict[str, Any]] = Field(default_factory=list)
```

Each entry preserves: `key`, `insight`, `confidence`, `sourceIdentifier`, `updatedAt`.  
Only insights where `active == true` are included. Sorted by `confidence` descending.  
Insights feed directly into the LLM prompt context alongside the brief and brand tokens.

### 3.5 BriefFacts Rebuild

After all overrides are applied to `FlatBrief` and `AvailableChannels`, `BriefFacts` is **rebuilt from the merged data**. The hallucination validator always checks against the post-merge values.

---

## 4. Precedence Rules

```
User Profile (live, authoritative from USP)
    │  wins if non-empty
    ▼
Brief Gate (pre-fetched by ROUTER at dispatch time)
    │  wins if non-empty and UP is silent
    ▼
Defaults / degraded mode
```

UP sets **who the client is** (identity, brand design, channels).  
Brief gate fills **how they want to communicate** (value prop, voice perspective, dates, wish text).  
Insights add **what we know about their performance and audience**.

---

## 5. Fetch Lifecycle

### 5.1 Where the fetch runs

USP is integrated **only into `POST /tasks`** — the async endpoint that writes to the DB. It is intentionally excluded from `POST /tasks/sync`, which remains a DB-free debug shortcut that uses brief-only context.

The fetch runs inside `_run_and_callback` — the async background task that executes after the 202 ACK is already sent.

```
POST /tasks → persist_on_ingest() → 202 ACCEPTED
    │
    └── Background worker (_run_and_callback):
         1. [NEW] Fetch User Profile (identity + insights)   (~300ms async)
              │
         2. [NEW] Persist USP response to raw_briefs row     (~5ms async)
              │
         3. Normalize  envelope + user_profile → InternalContext
              │
         4. Prompt     context + insights → Gemini prompt
              │
         5. LLM Call   Gemini structured output               (~10-14s)
              │
         6. Repair     if schema failure, 1 retry
              │
         7. Validate   deterministic checks
              │
         8. persist_on_complete()
              │
         9. PATCH callback_url
```

### 5.2 Lookup key

`payload.context.account_uuid` from the ROUTER envelope — the same UUID used as `users.id` in the persistence layer.

### 5.3 Execution model

The fetch runs **asynchronously** in the event loop before work is offloaded to the LLM thread:

```python
async def _run_and_callback(envelope, pctx=None):
    account_uuid = (envelope.get("payload") or {}).get("context", {}).get("account_uuid")

    user_profile = await fetch_user_profile(
        account_uuid=account_uuid,
        endpoint=settings.usp_graphql_url,
        api_key=settings.usp_api_key,
        timeout=settings.usp_timeout_seconds,
    )
    # user_profile is None on any failure; warn and continue

    if pctx and user_profile:
        await persist_user_profile(pctx.raw_brief_id, user_profile)

    def _sync_work() -> CallbackBody:
        return reason(envelope, gemini=client, user_profile=user_profile)

    callback = await asyncio.to_thread(_sync_work)
```

---

## 6. Persistence

### 6.1 What is stored

The raw USP response (both identity and insights) is stored as JSONB in `raw_briefs` alongside the ROUTER envelope. This gives a complete audit picture of what data was used in each enrichment run.

### 6.2 Schema change — migration `002`

A new nullable JSONB column is added to `raw_briefs`:

```python
# alembic/versions/002_add_user_profile_to_raw_briefs.py

op.add_column(
    "raw_briefs",
    sa.Column(
        "user_profile",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
)
```

No FK, no constraint — nullable because USP may be unavailable and older rows predating this migration have no UP data.

### 6.3 Write timing

`persist_on_ingest` runs before the 202 ACK and inserts the `raw_briefs` row with `user_profile = NULL`.  
After the USP fetch completes in the background worker, a new `update_raw_brief_user_profile(session, raw_brief_id, data)` repository call UPDATEs the row with the fetched data.  
The update is best-effort — if it fails, we log and continue. It must never block the LLM work.

### 6.4 Stored shape

```json
{
  "user_profile": {
    "fetched_at": "2026-04-22T15:30:00Z",
    "identity": { "uuid": "...", "brand": {...}, "company": {...}, "socialMedia": {...} },
    "insights": [
      { "uuid": "...", "key": "...", "insight": "...", "active": true, "confidence": 85 }
    ]
  }
}
```

`fetched_at` is added by the client (not from USP) so we can track freshness.

---

## 7. Configuration

### 7.1 New Environment Variables

All three variables are **required when enabling USP**. No hardcoded defaults — an empty `USP_API_KEY` disables the feature entirely.

| Variable | Required for USP | Description |
|---|---|---|
| `USP_GRAPHQL_URL` | **YES** | Full URL of the USP Memory Gateway GraphQL endpoint |
| `USP_API_KEY` | **YES** | `x-api-key` header value. Empty string = USP disabled |
| `USP_TIMEOUT_SECONDS` | No (default `5.0`) | HTTP timeout for the fetch. Keep low; failure degrades not blocks |

### 7.2 Settings class addition

```python
# config.py
usp_graphql_url: str = ""
usp_api_key: str = ""
usp_timeout_seconds: float = 5.0
```

Both URL and key default to empty string. If either is empty, the fetch is skipped and `user_profile_skipped` is emitted.

### 7.3 Secrets management

`USP_API_KEY` is stored in AWS Secrets Manager alongside `GEMINI_API_KEY` and `ORCH_CALLBACK_API_KEY`. See [07 — Security §3](./07-security.md) for the secrets injection pattern.

---

## 8. Failure Modes

| Condition | Behavior | Warning code |
|---|---|---|
| `USP_API_KEY` or `USP_GRAPHQL_URL` not configured | Skip fetch | `user_profile_skipped` |
| `account_uuid` absent in envelope | Skip fetch | `user_profile_skipped` |
| Network timeout (> `USP_TIMEOUT_SECONDS`) | Log warning; continue brief-only | `user_profile_unavailable` |
| HTTP 5xx from USP | Log warning; continue brief-only | `user_profile_unavailable` |
| GraphQL-level error (auth fail, resolver error) | Log warning; continue brief-only | `user_profile_unavailable` |
| `identity` returns null (account not in USP) | Log info; continue brief-only | `user_profile_not_found` |
| Persist update fails (DB error) | Log warning; enrichment continues normally | — |
| USP returns identity but all fields empty | Merge is no-op; brief values survive | — |

No USP failure ever causes the task to return `status: "FAILED"`.

The existing `degraded` flag in `TraceInfo` is **not** set for USP-related warnings. `degraded` is reserved for missing brief + empty gallery. USP unavailability is a softer degradation communicated only via warnings.

---

## 9. Implementation Scope

### 9.1 New files

**`src/marketer/user_profile.py`**  
GraphQL client + `UserProfile` dataclass + `UserInsight` dataclass. Single public surface:

```python
@dataclass
class UserInsight:
    key: str
    insight: str
    confidence: int | None
    source_identifier: str | None
    updated_at: str | None

@dataclass
class UserProfile:
    identity: IdentityData | None
    insights: list[UserInsight]
    fetched_at: str

async def fetch_user_profile(
    account_uuid: str,
    endpoint: str,
    api_key: str,
    timeout: float = 5.0,
) -> UserProfile | None:
    ...
```

No dependency on normalizer or reasoner. Importable standalone.

**`alembic/versions/002_add_user_profile_to_raw_briefs.py`**  
Adds nullable `user_profile` JSONB column to `raw_briefs`.

### 9.2 Modified files

**`src/marketer/config.py`** — add `usp_graphql_url`, `usp_api_key`, `usp_timeout_seconds`.

**`src/marketer/schemas/internal_context.py`**  
Add `user_insights: list[dict[str, Any]]` field to `InternalContext`.

**`src/marketer/normalizer.py`**
- `normalize()` gains `user_profile: UserProfile | None = None`
- New `_apply_user_profile()` applies overrides after brief extraction, before `brief_facts` is built
- When `flat_brief is None` but `user_profile is not None`, a minimal `FlatBrief()` is built from UP data alone
- Active insights are attached to `ctx.user_insights`

**`src/marketer/reasoner.py`**
- `reason()` gains `user_profile: UserProfile | None = None`, passes it to `normalize()`
- `_build_prompt_context()` includes `user_insights` in the JSON payload sent to Gemini

**`src/marketer/persistence.py`**  
New function `persist_user_profile(raw_brief_id, user_profile_data)` — async, best-effort UPDATE of `raw_briefs`.

**`src/marketer/db/repositories.py`**  
New repository function `update_raw_brief_user_profile(session, raw_brief_id, data)`.

**`src/marketer/main.py`**  
- `_run_and_callback()`: fetch UP async, persist it, pass to `reason()`
- `/tasks/sync` is **not** modified — it remains a DB-free dev tool

### 9.3 Not in scope

- Writing back to USP from Marketer — read-only integration
- Filtering insights by `key` type (all active insights are passed to LLM; prompt instructs selective use)
- Caching USP responses across tasks (each task fetches fresh)

---

## 10. Testing

### 10.1 UUID requirement

The Nubiex golden fixture uses a **synthetic UUID** (`f7a8b9c0-d1e2-4f3a-ab4b-5c6d7e8f9012`) that does not exist in USP. Live USP calls with this UUID will return `identity: null, insights: []` and emit `user_profile_not_found`.

For real integration testing, a **real account UUID** from the USP system is required. Set it via environment variable:

```bash
USP_TEST_ACCOUNT_UUID=<real-uuid-from-usp>
```

This is a development-time variable used only by integration test scripts. It is not consumed by the marketer app itself.

### 10.2 Unit tests (no network — mock `fetch_user_profile`)

All unit tests mock `fetch_user_profile` at the module boundary:

```python
# tests/test_normalizer_user_profile.py

from unittest.mock import AsyncMock, patch
from marketer.user_profile import UserProfile

@patch("marketer.main.fetch_user_profile", new_callable=AsyncMock)
async def test_up_overrides_brief_name(mock_fetch):
    mock_fetch.return_value = UserProfile(
        identity=IdentityData(name="UP Name", ...),
        insights=[],
        fetched_at="2026-04-22T00:00:00Z",
    )
    # assert ctx.brief.business_name == "UP Name"
```

Required test cases:
- `test_up_overrides_brief_field_by_field` — each FlatBrief field
- `test_up_empty_field_does_not_wipe_brief` — empty UP value leaves brief value intact
- `test_up_colors_override_palette_and_brief_facts` — hex validation + BriefFacts rebuild
- `test_up_insights_added_to_context` — active insights present, inactive filtered
- `test_up_none_normalize_unchanged` — existing normalize tests must be unaffected
- `test_up_brief_absent_up_present` — FlatBrief built from UP alone
- `test_user_profile_unavailable_warning` — warning emitted when fetch returns None
- `test_user_profile_skipped_warning` — warning emitted when no account_uuid or no key

### 10.3 Smoke test (brief-only degraded mode)

Without `USP_API_KEY` set:

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/quick_run.py
```

Should complete normally with `user_profile_skipped` warning. Output otherwise identical to pre-integration baseline.

### 10.4 Live integration test (requires real UUID + key)

```bash
USP_GRAPHQL_URL=<url> \
USP_API_KEY=<key> \
USP_TEST_ACCOUNT_UUID=<real-uuid> \
MARKETER_RUN_LIVE=1 \
PYTHONPATH=src python scripts/dev/quick_run.py
```

Verify in `reports/nubiex_dashboard.html`:
- No `user_profile_unavailable` warning
- `FlatBrief.business_name` matches USP `company.name`
- `BrandTokens.palette` reflects USP `brand.colors`
- Channels contain USP social media URLs
- `user_insights` list is non-empty and populated in the prompt context

---

## 11. Updated Architecture Diagram

```
POST /tasks
    │
    ├── persist_on_ingest() → raw_briefs row (user_profile=NULL)
    └── 202 ACCEPTED (~300ms)
              │
              └── Background worker (_run_and_callback):
                   │
                   ├─ 1. Fetch USP (identity + insights)     async ~300ms
                   │       │ on failure: warn + user_profile=None
                   │       ▼
                   ├─ 2. Persist UP → UPDATE raw_briefs      async ~5ms
                   │       │ best-effort; never blocks
                   │       ▼
                   ├─ 3. Normalize  envelope + user_profile
                   │       │ UP overrides brief fields
                   │       │ Insights → ctx.user_insights
                   │       │ BriefFacts rebuilt from merged data
                   │       ▼
                   ├─ 4. Prompt     context + insights → Gemini
                   │       ▼
                   ├─ 5. LLM Call   Gemini (~10-14s)
                   │       ▼
                   ├─ 6. Repair     if schema failure, 1 retry
                   │       ▼
                   ├─ 7. Validate   deterministic checks
                   │       ▼
                   ├─ 8. persist_on_complete()
                   │       ▼
                   └─ 9. PATCH callback_url
```

**Updated p50 end-to-end: ~12.5s** (USP fetch ~300ms adds to total; runs async before the LLM thread so it does not compound with LLM latency)
