# 08 — Integration Guide

**Version:** 2.0  
**Last Updated:** 2026-04-21

---

## 1. Overview

Marketer integrates with two systems:

1. **Upstream: ROUTER** — dispatches tasks to Marketer and receives enrichment results via callback
2. **Downstream: CONTENT_FACTORY** — receives `PostEnrichment` objects from ROUTER and generates final content

This document is the authoritative integration contract for both.

---

## 2. ROUTER Integration

### 2.1 Registration

Register Marketer in ROUTER's agent registry:

```sql
-- Register the agent
INSERT INTO agents (
    name,
    endpoint_url,
    auth_token,
    timeout_seconds,
    retry_attempts,
    supported_actions
) VALUES (
    'marketer',
    'https://marketer.internal.plinng.io',  -- ALB DNS or internal URL
    '{INBOUND_TOKEN}',                        -- same value as INBOUND_TOKEN env var
    90,                                        -- 90s timeout (covers p99 + buffer)
    2,                                         -- ROUTER-level retries
    ARRAY['create_post', 'edit_post']
);

-- Register step in agent sequence
INSERT INTO agent_sequence_steps (
    step_code,
    step_name,
    agent_id,
    step_order,
    is_required
) VALUES (
    'marketing_enrichment',
    'Marketing Enrichment',
    (SELECT id FROM agents WHERE name = 'marketer'),
    1,
    true
);
```

### 2.2 Dispatch Contract

ROUTER must send:

```
POST https://marketer.internal.plinng.io/tasks
Authorization: Bearer {INBOUND_TOKEN}
Content-Type: application/json
```

Minimum viable envelope:

```json
{
  "task_id": "<unique-task-uuid>",
  "action_code": "create_post",
  "callback_url": "https://router.internal/api/v1/tasks/<task_id>/callback",
  "payload": {
    "client_request": {
      "description": "<non-empty user request>"
    }
  }
}
```

Full production envelope includes:
- `payload.context` (account_uuid, client_name, platform)
- `payload.action_execution_gates.brief` (brand brief gate)
- `payload.action_execution_gates.image_catalog` (gallery gate)
- `payload.agent_sequence` (step metadata)

See [API Reference §2](./02-api-reference.md#post-tasks) for complete field specification.

### 2.3 Callback Endpoint

ROUTER must expose:

```
PATCH /api/v1/tasks/{task_id}/callback
X-API-Key: {ORCH_CALLBACK_API_KEY}
Content-Type: application/json
```

Receiving the `CallbackBody`:

```python
class CallbackHandler:
    def patch(self, task_id: str, body: CallbackBody):
        if body.status == "FAILED":
            self.handle_failure(task_id, body.error_message)
        elif body.output_data.trace.degraded:
            self.handle_degraded(task_id, body.output_data)
        else:
            self.handle_success(task_id, body.output_data.enrichment)
```

**Expected HTTP responses from ROUTER callback endpoint:**

| Response | Meaning |
|----------|---------|
| 200/201/204 | Accepted; Marketer stops |
| 404 | Task not found; Marketer does NOT retry |
| 409 | Conflict (duplicate callback); Marketer does NOT retry |
| 422 | Invalid body; Marketer does NOT retry |
| 5xx | Error; Marketer retries (up to CALLBACK_RETRY_ATTEMPTS) |

### 2.4 Timeout Handling

ROUTER's timeout for Marketer must be set to at least **90 seconds** to cover:
- p99 end-to-end latency (~30s)
- Callback delivery retries (~30s)
- Buffer

If ROUTER's task timeout fires before receiving the callback:
1. ROUTER marks task as timed-out
2. ROUTER may retry by sending a new POST /tasks with the same `task_id`
3. Marketer will process the retry normally (no deduplication in MVP)
4. ROUTER may receive two callbacks for the same `task_id` — handle with idempotency on the callback endpoint (409 on duplicate)

### 2.5 Brief Gate Structure

Marketer reads the brief from:

```json
payload.action_execution_gates.brief = {
  "passed": true,
  "status_code": 200,
  "response": {
    "data": {
      "form_values": {
        "FIELD_COMPANY_NAME": "...",
        "FIELD_COLOR_LIST_PICKER": "#hex1,#hex2,...",
        "FIELD_COMMUNICATION_STYLE": "...",
        "FIELD_VALUE_PROPOSITION": "...",
        ...
      },
      "profile": {
        "website_url": "https://...",
        "instagram_url": "https://...",
        "phone_number": "...",
        ...
      }
    }
  }
}
```

**When `passed = false`:** Marketer emits `brief_missing` warning and proceeds in degraded mode. The enrichment will have generic defaults instead of brand-specific values.

**Important:** Marketer does NOT fail if the brief gate is missing. It degrades gracefully. Only `edit_post` without `prior_post` and unsupported `action_code` result in FAILED.

### 2.6 Gallery Gate Structure

```json
payload.action_execution_gates.image_catalog = {
  "passed": true,
  "status_code": 200,
  "response": {
    "data": [
      {
        "url": "https://cdn.example.com/image.jpg",
        "role": "content",
        "tags": ["food", "lifestyle"],
        "width": 1080,
        "height": 1080,
        "size_bytes": 450000
      }
    ]
  }
}
```

**Gallery image requirements:**
- URL must be `http(s)://`
- Extension: `jpg`, `jpeg`, `png`, `webp`
- `size_bytes` < 20 MB (20,971,520 bytes)
- Non-conforming images are rejected with warning (not fatal)
- Maximum 20 images (excess truncated)

### 2.7 Edit Post Contract

For `edit_post`, ROUTER must include prior post context:

```json
{
  "action_code": "edit_post",
  "payload": {
    "context": {
      "post_id": "instagram-post-id-123",
      "prior_post": {
        "caption": "Original published caption...",
        "image_url": "https://cdn.example.com/original.jpg"
      }
    }
  }
}
```

If `prior_post` is missing or empty: returns FAILED with `prior_post_missing`.
If `post_id` is missing: proceeds with warning `context_missing_id`.

---

## 3. CONTENT_FACTORY Integration

### 3.1 PostEnrichment Consumption

CONTENT_FACTORY receives the enrichment from ROUTER (which stores the CallbackBody). The key fields for content generation:

#### Public Fields (primary use)

```python
enrichment = PostEnrichment(...)

# What format to create
format = enrichment.surface_format  # "post" | "story" | "reel" | "carousel"
pillar = enrichment.content_pillar  # content category

# What to publish (ready-to-use)
hook = enrichment.caption.hook      # first line
body = enrichment.caption.body      # main content
cta  = enrichment.caption.cta_line  # call-to-action line

# Publish button
cta_channel = enrichment.cta.channel        # "website" | "dm" | ...
cta_url     = enrichment.cta.url_or_handle  # nullable
cta_label   = enrichment.cta.label          # button text

# Image generation
image_prompt  = enrichment.image.generation_prompt
image_concept = enrichment.image.concept
image_alt     = enrichment.image.alt_text

# Asset selection
use_assets = enrichment.visual_selection.recommended_asset_urls
use_refs   = enrichment.visual_selection.recommended_reference_urls
skip_assets = enrichment.visual_selection.avoid_asset_urls

# Hashtags (ready to append)
hashtags = enrichment.hashtag_strategy.tags  # ["#casa", "#restaurant", ...]

# Design reference
brand_dna = enrichment.brand_dna  # 200-400 words for CF design system

# Full brief for CF
cf_brief = enrichment.cf_post_brief  # editorial note + caption + hashtags
```

#### Structural Constraints

- `visual_selection.recommended_asset_urls` are always a subset of the original gallery
- `hashtag_strategy.tags` always have `#` prefix, 5–10 items
- `cta.url_or_handle` is null for channels `dm`, `link_sticker`, `none`
- `do_not` list is capped at 5 items
- `schema_version` is always `"2.0"` — check before parsing

### 3.2 Handling Degraded Enrichments

When `trace.degraded == true`, the enrichment is valid but derived from incomplete brand data:

```python
if trace.degraded:
    # One or more of: brief_missing, gallery_empty, gallery_all_filtered
    relevant_warnings = [w for w in warnings if w.code in {
        "brief_missing", "gallery_empty", "gallery_all_filtered"
    }]
    # Options:
    # 1. Proceed with degraded enrichment (generic brand values)
    # 2. Request user to complete brief before retrying
    # 3. Apply a default template instead
```

### 3.3 CTA Integration

```python
cta = enrichment.cta

match cta.channel:
    case "website":
        button_url = cta.url_or_handle     # always a full URL
        button_label = cta.label
    case "phone" | "whatsapp":
        phone = cta.url_or_handle
        button_label = cta.label
    case "dm" | "link_sticker":
        # No URL; use native platform mechanism
        button_label = cta.label
    case "none":
        # Awareness post; no button
        pass
```

### 3.4 Visual Selection Logic

```python
selection = enrichment.visual_selection

# Primary images for the post
primary_assets = [
    img for img in gallery
    if img.url in selection.recommended_asset_urls
]

# Style reference images (not for publishing)
style_refs = [
    img for img in gallery
    if img.url in selection.recommended_reference_urls
]

# Images to avoid
avoid_set = set(selection.avoid_asset_urls)
usable_gallery = [img for img in gallery if img.url not in avoid_set]
```

---

## 4. Environment Setup for Integration Testing

### 4.1 Local Integration Test (smoke_async_roundtrip.py)

```bash
# Start Marketer locally
GEMINI_API_KEY=your-key \
PYTHONPATH=src \
uvicorn marketer.main:app --port 8080 &

# Run smoke test (starts mock callback server)
PYTHONPATH=src python scripts/ops/smoke_async_roundtrip.py

# Expected output:
# ✓ POST /tasks → 202 ACCEPTED
# ✓ Background task started
# ✓ Gemini call completed (12.3s)
# ✓ PATCH callback received: status=COMPLETED
# ✓ Schema version: 2.0
# ✓ CTA channel: dm (matches available_channels)
# ✓ Visual selection URLs in gallery
```

### 4.2 Sync Endpoint for Development

Use `/tasks/sync` during integration development to get inline responses:

```bash
curl -X POST http://localhost:8080/tasks/sync \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/envelopes/casa_maruja_post.json \
  | python3 -m json.tool
```

### 4.3 Test Fixtures

Available test envelopes in `tests/fixtures/envelopes/`:

| Fixture | Use Case | Notes |
|---------|----------|-------|
| `casa_maruja_post.json` | Rich brief, gallery, create_post | Golden baseline |
| `divenamic_create_post.json` | E-commerce brand | Multi-field brief |
| `retail_ecom_post.json` | Clothing brand | Image-heavy gallery |
| `dentist_post.json` | Professional services | Conservative tone |
| `saas_b2b_post.json` | B2B SaaS | Technical audience |
| `minimal_post.json` | Sparse brief | Degraded mode test |
| `missing_brief_post.json` | No brief gate | brief_missing warning |
| `edit_post_no_id.json` | Error case | FAILED response test |
| `fontaneria_web.json` | Web action | FAILED (gated) test |

---

## 5. Error Handling Patterns

### 5.1 ROUTER Error Handling

```python
async def handle_marketer_callback(task_id: str, body: CallbackBody):
    match body.status:
        case "COMPLETED":
            if body.output_data.trace.degraded:
                # Store enrichment + degraded flag
                # Notify orchestrator for possible user prompt
                await store_enrichment(task_id, body.output_data, degraded=True)
            else:
                await store_enrichment(task_id, body.output_data, degraded=False)

        case "FAILED":
            error = body.error_message
            match error:
                case error if error.startswith("unsupported_action_code"):
                    # Route to different agent
                    await route_to_fallback_agent(task_id)
                case "create_web_not_supported_in_this_iteration":
                    # Fail user-facing request gracefully
                    await notify_user_feature_unavailable(task_id)
                case error if error.startswith("prior_post_missing"):
                    # Request ROUTER to re-fetch prior post
                    await request_prior_post_and_retry(task_id)
                case _:
                    # Generic failure; retry once
                    await schedule_retry(task_id)
```

### 5.2 CONTENT_FACTORY Validation

CF should validate before using the enrichment:

```python
def validate_enrichment(enrichment: dict) -> tuple[bool, list[str]]:
    errors = []

    if enrichment.get("schema_version") != "2.0":
        errors.append(f"Unknown schema version: {enrichment.get('schema_version')}")
        return False, errors

    if not enrichment.get("caption", {}).get("hook"):
        errors.append("Empty caption hook")

    if not enrichment.get("cf_post_brief"):
        errors.append("Missing cf_post_brief")

    return len(errors) == 0, errors
```

---

## 6. Version Compatibility

### 6.1 Schema Versioning

All enrichments include `schema_version: "2.0"`. ROUTER and CF must:

1. Check `schema_version` before parsing
2. Reject unknown versions with an error
3. Never assume schema compatibility across major versions

### 6.2 Action Code Compatibility

| Action Code | Marketer | ROUTER | CF |
|-------------|----------|--------|-----|
| `create_post` | ✅ | ✅ | ✅ |
| `edit_post` | ✅ | ✅ | ✅ |
| `create_web` | ❌ (FAILED) | TBD | TBD |
| `edit_web` | ❌ (FAILED) | TBD | TBD |

### 6.3 Channel Kind Compatibility

When adding new `ChannelKind` values:

1. Add to `enrichment.py` `ChannelKind` enum
2. Update `normalizer.py` to recognize new channel from brief
3. Update `validator.py` to handle new channel's URL requirements
4. Update `llm/prompts/system.py` to include new channel in constraints
5. Notify CONTENT_FACTORY of new channel before deploying

New channel values that CF doesn't handle will result in unrendered CTAs.

---

## 7. Observability for Integrators

### 7.1 Trace Fields

The `trace` object in every callback provides integration diagnostics:

```json
{
  "trace": {
    "task_id": "...",         // matches request task_id
    "action_code": "...",     // what was processed
    "surface": "post|web",   // resolved surface
    "mode": "create|edit",   // resolved mode
    "latency_ms": 12340,     // total processing time
    "gemini_model": "...",   // which model was used
    "repair_attempted": false, // was schema repair needed?
    "degraded": false,         // weak input signals?
    "gallery_stats": {
      "raw_count": 10,
      "accepted_count": 8,
      "rejected_count": 2,
      "truncated": false
    }
  }
}
```

### 7.2 Warning Analysis

ROUTER should track warning distribution to identify data quality issues:

```sql
-- Warning frequency by code (from stored callbacks)
SELECT warning_code, COUNT(*) as n
FROM marketer_callbacks,
     LATERAL jsonb_array_elements(output_data->'warnings') AS w(obj),
     LATERAL (SELECT obj->>'code' as warning_code) q
GROUP BY warning_code
ORDER BY n DESC;
```

High `brief_missing` rate → brief gate not being called properly.
High `gallery_empty` rate → image_catalog gate failing.
High `cta_channel_invalid` rate → brief contact info format issue.
High `schema_repair_used` rate → prompt/model regression; alert Marketer team.
