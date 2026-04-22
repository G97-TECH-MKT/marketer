# 02 — API Reference

**Version:** 2.0  
**Last Updated:** 2026-04-21  
**Base URL:** `https://{host}` (no path prefix)

---

## Authentication

All production requests must include:

```
Authorization: Bearer {INBOUND_TOKEN}
```

- If `INBOUND_TOKEN` env var is empty (dev mode), the header is ignored.
- If `INBOUND_TOKEN` is set and the header is missing or mismatched, the request returns `401`.
- Token is a shared secret configured at deploy time. Treat as a service-to-service credential.

---

## Endpoints

### POST /tasks

**Purpose:** Primary ingress. Accepts task envelopes from ROUTER, returns 202 immediately, processes asynchronously, and delivers results via `PATCH callback_url`.

#### Request

```
POST /tasks HTTP/1.1
Content-Type: application/json
Authorization: Bearer {INBOUND_TOKEN}
```

**Body:** `RouterEnvelope` (see [Data Models §2.1](./03-data-models.md#21-routerenvelope))

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "job_id": "job-uuid-optional",
  "action_code": "create_post",
  "callback_url": "https://router.internal/api/v1/tasks/550e8400/callback",
  "correlation_id": "corr-optional",
  "action_id": "action-uuid-optional",
  "payload": {
    "client_request": {
      "description": "Crea un post para el menú del domingo",
      "attachments": [
        "https://cdn.example.com/attachments/f14bbec5/photo-1.jpg",
        "https://cdn.example.com/attachments/f14bbec5/photo-2.jpg"
      ]
    },
    "context": {
      "account_uuid": "acct-uuid",
      "client_name": "Casa Maruja",
      "platform": "instagram",
      "post_id": null,
      "prior_post": null
    },
    "action_execution_gates": {
      "brief": {
        "passed": true,
        "status_code": 200,
        "response": {
          "data": {
            "form_values": {
              "FIELD_COMPANY_NAME": "Casa Maruja",
              "FIELD_COLOR_LIST_PICKER": "#E8D5B7,#2C1810,#F5F0E8",
              "FIELD_COMMUNICATION_STYLE": "Cálida y familiar"
            }
          }
        }
      },
      "image_catalog": {
        "passed": true,
        "status_code": 200,
        "response": {
          "data": [
            {
              "url": "https://cdn.example.com/paella.jpg",
              "role": "content",
              "tags": ["food", "spanish", "paella"],
              "width": 1080,
              "height": 1080,
              "size_bytes": 450000
            }
          ]
        }
      }
    },
    "agent_sequence": {
      "current": {
        "step_code": "marketing_enrichment",
        "step_order": 1
      },
      "previous": {}
    }
  }
}
```

#### Required Fields

| Path | Type | Notes |
|------|------|-------|
| `task_id` | string | UUID or opaque string; used in logs and callback trace |
| `action_code` | string | `create_post`, `edit_post`, `create_web`, `edit_web` |
| `callback_url` | string | Full HTTPS URL for PATCH result delivery |
| `payload.client_request.description` | string | Non-empty user request text |

#### Optional Fields

| Path | Notes |
|------|-------|
| `job_id` | Logged; helps ROUTER correlate jobs |
| `action_id` | Logged; identifies action record |
| `correlation_id` | Logged; passed through in trace |
| `payload.context` | Account/client metadata; if missing, enrichment is degraded |
| `payload.client_request.attachments` | Optional list of image URLs (`list[str]`) |
| `payload.action_execution_gates.brief` | Brand brief; if missing, `brief_missing` warning, degraded=true |
| `payload.action_execution_gates.image_catalog` | Gallery; if missing, `gallery_empty` warning, degraded=true |
| `payload.context.prior_post` | Required for `edit_post`; missing triggers FAILED |
| `payload.context.post_id` | Required for `edit_post`; generates warning if missing |

#### Successful Response

```
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "status": "ACCEPTED",
  "task_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

#### Error Responses

| Code | Condition | Body |
|------|-----------|------|
| `400` | Malformed JSON | `{"detail": "invalid_json: <parse error>"}` |
| `400` | Envelope not an object | `{"detail": "envelope must be an object"}` |
| `400` | `task_id` missing or empty | `{"detail": "task_id is required"}` |
| `400` | `callback_url` missing or empty | `{"detail": "callback_url is required"}` |
| `400` | `action_code` missing | `{"detail": "action_code is required"}` |
| `401` | Auth header missing or wrong token | `{"detail": "invalid_token"}` |
| `503` | `GEMINI_API_KEY` not configured | `{"detail": "GEMINI_API_KEY not configured"}` |

#### Async Result (PATCH callback_url)

After the background pipeline completes (typically 10–18s), Marketer sends:

```
PATCH {callback_url} HTTP/1.1
Content-Type: application/json
X-API-Key: {ORCH_CALLBACK_API_KEY}
```

**Body:** `CallbackBody`

```json
{
  "status": "COMPLETED",
  "output_data": {
    "enrichment": { ...PostEnrichment v2.0... },
    "warnings": [
      {
        "code": "gallery_partially_filtered",
        "message": "2 of 10 gallery items were rejected (invalid URL or extension)",
        "field": "gallery"
      }
    ],
    "trace": {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "action_code": "create_post",
      "surface": "post",
      "mode": "create",
      "latency_ms": 12340,
      "gemini_model": "gemini-2.5-flash-preview",
      "repair_attempted": false,
      "degraded": false,
      "gallery_stats": {
        "raw_count": 10,
        "accepted_count": 8,
        "rejected_count": 2,
        "truncated": false
      }
    }
  },
  "error_message": null
}
```

**Failed callback:**

```json
{
  "status": "FAILED",
  "output_data": {
    "enrichment": null,
    "warnings": [],
    "trace": {
      "task_id": "550e8400-...",
      "action_code": "create_web",
      "surface": "web",
      "mode": "create",
      "latency_ms": 5,
      "gemini_model": "gemini-2.5-flash-preview",
      "repair_attempted": false,
      "degraded": false,
      "gallery_stats": null
    }
  },
  "error_message": "create_web_not_supported_in_this_iteration"
}
```

**Callback retry policy:**

| Scenario | Behavior |
|----------|----------|
| HTTP 2xx | Success; no retry |
| HTTP 5xx | Retry (up to `CALLBACK_RETRY_ATTEMPTS`) with exponential backoff |
| HTTP 408, 429 | Retry |
| HTTP 4xx (except 408/429) | Terminal; log and abandon |
| Timeout / connection error | Retry |
| All retries exhausted | Log `callback_failed_after_N_attempts`; task lost |

---

### POST /tasks/sync

**Purpose:** Development and testing only. Same input as `/tasks`; returns the full `CallbackBody` inline (no background task, no actual callback dispatch).

> **Warning:** Do NOT route production traffic here. This endpoint blocks for the full LLM duration (~12–18s) and is not rate-limited.

#### Request

Same as `POST /tasks`.

#### Response

```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "COMPLETED",
  "output_data": {
    "enrichment": { ...PostEnrichment v2.0... },
    "warnings": [...],
    "trace": {...}
  },
  "error_message": null
}
```

Errors return the same 4xx/5xx codes as `/tasks`, plus `FAILED` inline responses for async errors (instead of via callback).

---

### GET /health

**Purpose:** Liveness probe. Always returns 200 if the process is running.

**No auth required.**

#### Response

```
HTTP/1.1 200 OK
Content-Type: application/json

{"status": "healthy"}
```

---

### GET /ready

**Purpose:** Readiness probe. Returns 200 only if the service is fully operational.

**No auth required.**

**Gates on:**
1. `GEMINI_API_KEY` is set (non-empty)
2. Database connection (if `DATABASE_URL` is set)

#### Success Response

```
HTTP/1.1 200 OK
Content-Type: application/json

{"status": "ready"}
```

#### Failure Responses

```
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"status": "unhealthy", "detail": "GEMINI_API_KEY not set"}
```

```json
{"status": "unhealthy", "detail": "database connection failed: <error>"}
```

---

## Error Code Reference

### Sync Error Codes (HTTP 4xx/5xx)

| Code | `detail` value | Meaning |
|------|---------------|---------|
| 400 | `invalid_json: ...` | Request body is not valid JSON |
| 400 | `envelope must be an object` | Body is valid JSON but not an object |
| 400 | `task_id is required` | `task_id` field missing or empty string |
| 400 | `callback_url is required` | `callback_url` field missing or empty string |
| 400 | `action_code is required` | `action_code` field missing |
| 401 | `invalid_token` | Bearer token mismatch or header absent |
| 503 | `GEMINI_API_KEY not configured` | API key env var not set |

### Async Error Codes (in `CallbackBody.error_message`)

| Value | Trigger |
|-------|---------|
| `unsupported_action_code: <code>` | `action_code` not in supported set |
| `create_web_not_supported_in_this_iteration` | `create_web` or `edit_web` received |
| `prior_post_missing: <detail>` | `edit_post` without `prior_post` in context |
| `schema_validation_failed: <error>` | Gemini output not parseable after repair |
| `internal_error: asyncio.TimeoutError: ...` | Gemini call exceeded `LLM_TIMEOUT_SECONDS` |
| `internal_error: <ExceptionType>: <msg>` | Unhandled pipeline exception |

### Warning Codes (in `CallbackBody.output_data.warnings[].code`)

| Code | Severity | Degraded? |
|------|----------|-----------|
| `brief_missing` | HIGH | ✅ Yes |
| `gallery_empty` | HIGH | ✅ Yes |
| `gallery_all_filtered` | HIGH | ✅ Yes |
| `value_proposition_empty` | HIGH | No |
| `claim_not_in_brief` | HIGH | No |
| `palette_mismatch` | HIGH | No |
| `visual_hallucinated` | HIGH | No |
| `cta_channel_invalid` | HIGH | No |
| `brief_field_missing` | MEDIUM | No |
| `tone_unclear` | MEDIUM | No |
| `context_missing_id` | MEDIUM | No |
| `brief_request_mismatch` | MEDIUM | No |
| `price_not_in_brief` | MEDIUM | No |
| `cta_caption_channel_mismatch` | MEDIUM | No |
| `caption_length_exceeded` | MEDIUM | No |
| `gallery_partially_filtered` | LOW | No |
| `gallery_truncated` | LOW | No |
| `request_vague` | LOW | No |
| `surface_format_overridden` | LOW | No |
| `schema_repair_used` | LOW | No |
| `do_not_truncated` | LOW | No |
| `field_missing` | LOW | No |
| `reference_used_as_asset` | LOW | No |

---

## Rate Limits & Quotas

Marketer does not enforce its own rate limits. However:

- **Gemini API quota**: Default free tier is 15 RPM / 1M TPM. Production needs a paid quota increase.
- **Concurrent tasks per replica**: ~10–20 (constrained by threadpool for `asyncio.to_thread()`).
- **Gallery images per request**: Hard cap of 20 images (excess truncated with warning).
- **Prompt tokens**: ~3,000–5,000 per request; large galleries or briefs can push toward 8,000.

---

## Headers Reference

### Inbound (ROUTER → Marketer)

| Header | Required | Notes |
|--------|----------|-------|
| `Content-Type` | Yes | Must be `application/json` |
| `Authorization` | Prod | `Bearer {INBOUND_TOKEN}` |

### Outbound (Marketer → ROUTER callback)

| Header | Present When | Notes |
|--------|-------------|-------|
| `Content-Type` | Always | `application/json` |
| `X-API-Key` | `ORCH_CALLBACK_API_KEY` set | ROUTER validates this |

---

## Idempotency

`POST /tasks` is **not idempotent**. Duplicate `task_id` submissions result in duplicate background tasks and multiple callbacks to the same `callback_url`. The ROUTER is responsible for deduplication if needed.

`POST /tasks/sync` is idempotent per invocation (stateless; each call triggers a fresh Gemini call).

---

## OpenAPI Schema (programmatic)

The FastAPI app auto-generates an OpenAPI schema at runtime:

```
GET /openapi.json
GET /docs       (Swagger UI, dev only)
GET /redoc      (ReDoc UI, dev only)
```

In production, disable Swagger/ReDoc:

```python
app = FastAPI(docs_url=None, redoc_url=None)
```

This is already configured in `main.py` when `LOG_LEVEL != DEBUG`.
