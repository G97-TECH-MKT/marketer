# 12 — Subscription Strategy (Multi-Job Action)

**Version:** 1.0  
**Last Updated:** 2026-04-23  
**Status:** Spec — implementation pending

---

## 1. Overview

### 1.1 What Is subscription_strategy?

`subscription_strategy` is a new `action_code` that allows the ROUTER to dispatch **multiple content jobs in a single envelope**. Instead of sending N separate envelopes (one per post), ROUTER sends one envelope containing an array of jobs. Marketer makes **one LLM call** that produces N `PostEnrichment` objects, each persisted and reported independently.

### 1.2 Why?

- **Token efficiency**: A single LLM call with shared brand context is cheaper and faster than N independent calls.
- **Strategic coherence**: The LLM sees all jobs together and can vary content pillars, angles, and surface formats across the batch to avoid repetition.
- **Subscription planning**: Enables ROUTER to dispatch a full content calendar (e.g., "4 posts this week") as a single orchestration step.

### 1.3 How It Differs from Single-Job Flow

| Aspect | Single-job (create_post, etc.) | Multi-job (subscription_strategy) |
|--------|-------------------------------|----------------------------------|
| Envelope → LLM calls | 1 envelope → 1 LLM call → 1 PostEnrichment | 1 envelope → 1 LLM call → N PostEnrichments |
| `client_request` | `{description, attachments}` | `{description, jobs: [...]}` |
| LLM output shape | `PostEnrichment` (single object) | `{items: [PostEnrichment, ...]}` |
| DB jobs created | 1 | N (one per valid job) |
| Callbacks sent | 1 PATCH | N PATCHes (one per job) |
| `jobs.orchestrator_agent` | NULL | `"job-router"` or `"prod-line"` |

---

## 2. Envelope Contract

### 2.1 Input Shape

The envelope follows the standard ROUTER dispatch contract (see `docs/ROUTER CONTRACT.md` §3) with these differences:

- `action_code` is `"subscription_strategy"`
- `payload.client_request` contains a `jobs` array instead of a single `description`

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "job_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "action_code": "subscription_strategy",
  "action_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
  "correlation_id": "sub-20260423-nubiex-weekly",
  "callback_url": "https://router.internal/api/v1/tasks/a1b2c3d4/callback",
  "payload": {
    "client_request": {
      "description": "Genera la estrategia de contenido semanal para Nubiex.",
      "jobs": [
        {
          "action_key": "create_post",
          "description": "Posts sobre los beneficios del masaje consciente para hombres. Tono intimo y seguro.",
          "quantity": 2,
          "slug": "POST-INSTAGRAM",
          "orchestrator_agent": "job-router",
          "product_uuid": "prod-uuid-001"
        },
        {
          "action_key": "create_post",
          "description": "Carousel educativo: 3 mitos sobre el masaje masculino y la verdad detras de cada uno.",
          "quantity": 1,
          "slug": "POST-INSTAGRAM",
          "orchestrator_agent": "job-router",
          "product_uuid": "prod-uuid-002"
        }
      ],
      "attachments": []
    },
    "context": {
      "account_uuid": "f7a8b9c0-d1e2-4f3a-ab4b-5c6d7e8f9012",
      "client_name": "Nubiex Men's Massage by Bruno",
      "platform": "instagram"
    },
    "action_execution_gates": {
      "brief": { "...same shape as single-job..." },
      "image_catalog": { "...same shape as single-job..." }
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

### 2.2 `client_request.jobs[]` Item Shape

Each job in the array has:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action_key` | string | **yes** | The action_code for this specific job (e.g., `"create_post"`). Must be a valid, enabled action in `action_types`. |
| `description` | string | **yes** | User request text for this specific job. |
| `quantity` | int | no (default 1) | How many PostEnrichments to generate for this job. A job with `quantity: 2` produces 2 distinct, varied enrichments. Max 10. |
| `slug` | string | no | Product slug from the router (e.g., `"POST-INSTAGRAM"`). Stored for traceability. |
| `orchestrator_agent` | string | no | Which orchestrator dispatched this job (`"job-router"` or `"prod-line"`). Stored in `jobs.orchestrator_agent` column. |
| `product_uuid` | string | no | Product UUID from the subscription. Stored for traceability. |

**Quantity expansion:** A job with `quantity: N` is expanded into N `SubscriptionJob` entries internally. Each produces an independent PostEnrichment. The LLM sees N items in the prompt and is instructed to vary content_pillar, angle, surface_format, and rhetorical_device across them.

**Notes:**
- `action_key` is validated against `action_types`. Jobs with unknown or disabled `action_key` are **skipped** — they do not produce a PostEnrichment and do not get a callback. A warning is emitted.
- `client_request.description` (top-level) serves as the **overall strategy description** and is passed to the LLM as context. Each `jobs[].description` is the per-job instruction.
- `attachments` remain at the `client_request` level (shared across all jobs). Per-job attachments are not supported in v1.
- `context`, `action_execution_gates`, and `agent_sequence` are **shared** across all jobs (same brand, same brief, same gallery).

### 2.3 Required Fields

Same as single-job, plus:

| Path | Type | Notes |
|------|------|-------|
| `payload.client_request.jobs` | `list[object]` | Non-empty array. At least 1 job with valid `action_key`. |
| `payload.client_request.jobs[].action_key` | string | Must be in `action_types` and `is_enabled=true`. |
| `payload.client_request.jobs[].description` | string | Non-empty. |

---

## 3. Data Models

### 3.1 SubscriptionJob (internal)

**File:** `src/marketer/schemas/internal_context.py`

```python
class SubscriptionJob(BaseModel):
    action_key: str                    # action_code of the child job
    description: str                   # per-job user request
    index: int                         # sequential index post-expansion (0-based)
    quantity: int = 1                  # original quantity from the router job
    slug: str | None = None            # product slug (e.g., "POST-INSTAGRAM")
    orchestrator_agent: str | None = None  # "job-router" or "prod-line"
    product_uuid: str | None = None    # product UUID from subscription
```

Added as optional field on `InternalContext`:

```python
subscription_jobs: list[SubscriptionJob] | None = None
```

### 3.2 MultiEnrichmentOutput (LLM response)

**File:** `src/marketer/schemas/enrichment.py`

```python
class MultiEnrichmentOutput(BaseModel):
    """LLM response for subscription_strategy: one PostEnrichment per job."""
    items: list[PostEnrichment]
```

The LLM returns this wrapper. Marketer parses it client-side with `MultiEnrichmentOutput.model_validate_json(raw_text)`. The GeminiClient itself does **not** change — it still uses JSON mode (`response_mime_type="application/json"` only, no `response_schema`).

### 3.3 DB: `orchestrator_agent` Column

**Table:** `jobs`  
**Column:** `orchestrator_agent TEXT NULL`  
**CHECK:** `orchestrator_agent IN ('job-router', 'prod-line')`

| Value | Meaning |
|-------|---------|
| `NULL` | Legacy single-job flow (create_post, edit_post, etc.) |
| `job-router` | Job created by subscription_strategy, dispatched by job-router |
| `prod-line` | Job created by subscription_strategy, dispatched by prod-line |

### 3.4 DB: `action_types` Row

```sql
INSERT INTO action_types (code, surface, mode, label, prompt_overlay, is_enabled)
VALUES ('subscription_strategy', 'other', 'create', 'Subscription Strategy', 'subscription_strategy', true);
```

### 3.5 Migration

**File:** `alembic/versions/003_subscription_strategy.py`

1. `ALTER TABLE jobs ADD COLUMN orchestrator_agent TEXT NULL`
2. `ALTER TABLE jobs ADD CONSTRAINT jobs_orchestrator_agent_check CHECK (orchestrator_agent IN ('job-router', 'prod-line'))`
3. `INSERT INTO action_types ...` (as above)

---

## 4. Processing Pipeline

```
POST /tasks (202 ACK, same as single-job)
    |
    +-- Background worker:
         1. Normalize    envelope -> InternalContext + subscription_jobs[]
         2. Filter       keep only jobs with valid, enabled action_key
         3. Build prompt overlay=SUBSCRIPTION_STRATEGY_OVERLAY + context + jobs list
         4. LLM call     Gemini -> {"items": [PostEnrichment, ...]}
         5. Parse        MultiEnrichmentOutput.model_validate_json(raw_text)
         6. Repair       if parse fails, same repair cycle as single-job
         7. Validate     validate_and_correct(enrichment, ctx) for EACH item
         8. Persist      ensure_strategy once; create_job for EACH item
         9. Callback     PATCH callback_url once per item (N PATCHes)
```

### 4.1 Filtering

Jobs are filtered **before** the LLM call:

1. Parse `client_request.jobs[]` into `list[SubscriptionJob]`
2. For each job, check `action_key` against `action_types` cache:
   - If not found or `is_enabled=false` → skip, emit warning `job_action_key_invalid`
3. Only valid jobs are serialized into the LLM prompt
4. If zero valid jobs remain → FAIL the entire task

### 4.2 LLM Call

One Gemini call produces all enrichments. The system prompt and overlay instruct the LLM to:
- Return `{"items": [PostEnrichment, PostEnrichment, ...]}`
- Maintain the same order as the input jobs
- Vary content_pillar, surface_format, and angle across items to avoid repetition
- Share brand_dna across items (same brand identity)

### 4.3 Validation

Each `PostEnrichment` in `items[]` is validated independently via `validate_and_correct()`. A failing item does not block other items. Failed items produce a FAILED callback; successful items produce COMPLETED callbacks.

---

## 5. Callback Contract

### 5.1 N Individual PATCHes

Each job result is delivered as a **separate PATCH** to the same `callback_url`. The body is a standard `CallbackBody` with an additional `job_index` field in `trace`:

```json
{
  "status": "COMPLETED",
  "output_data": {
    "data": {
      "total_items": 1,
      "client_dna": "...",
      "client_request": "...",
      "resources": [...]
    },
    "enrichment": { "...PostEnrichment v2.0..." },
    "warnings": [],
    "trace": {
      "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "action_code": "subscription_strategy",
      "surface": "post",
      "mode": "create",
      "latency_ms": 14200,
      "gemini_model": "gemini-2.5-flash-preview",
      "repair_attempted": false,
      "degraded": false,
      "gallery_stats": { "raw_count": 5, "accepted_count": 5, "rejected_count": 0, "truncated": false },
      "job_index": 0,
      "job_action_key": "create_post",
      "total_jobs": 3
    }
  },
  "error_message": null
}
```

### 5.2 New Trace Fields

| Field | Type | Description |
|-------|------|-------------|
| `job_index` | int | 0-based index in the original `jobs[]` array |
| `job_action_key` | string | `action_key` of this specific job |
| `total_jobs` | int | Total number of valid jobs in the batch |

These fields allow the ROUTER to correlate each callback to its source job.

### 5.3 Callback Ordering

Callbacks are sent **sequentially** in the order of `jobs[]`. Each PATCH waits for the previous one to complete (or exhaust retries) before sending the next. This is a simplicity choice for v1; parallel dispatch is a future optimization.

### 5.4 Partial Failure

If some enrichments succeed and others fail:
- Successful items → PATCH with `status: "COMPLETED"`
- Failed items → PATCH with `status: "FAILED"` and `error_message`
- The raw_brief is marked `"done"` if at least one item succeeded, `"failed"` if all failed

---

## 6. Prompt Overlay

**File:** `src/marketer/llm/prompts/subscription_strategy.py`

The overlay instructs the LLM to:
- Receive N job descriptions with their `action_key` and `description`
- Produce one `PostEnrichment` per job, wrapped in `{"items": [...]}`
- Maintain input order
- Vary strategic choices across items (different content_pillar, surface_format, angle)
- Share `brand_dna` (same brand identity for all items)
- Each item follows the exact same PostEnrichment v2.0 schema

The system prompt (`llm/prompts/system.py`) gets a conditional section:
> "When the overlay requests multi-item output, return `{"items": [PostEnrichment, ...]}` instead of a single PostEnrichment object."

---

## 7. Error Handling

| Scenario | Behavior |
|----------|----------|
| `client_request.jobs` missing or empty | 422 sync error: `"jobs array is required for subscription_strategy"` |
| All `action_key` values invalid | FAILED callback: `"no valid jobs: all action_keys unknown or disabled"` |
| LLM returns fewer items than expected | Warning `job_count_mismatch`; available items are matched by index |
| LLM returns more items than expected | Extra items are discarded |
| Individual enrichment fails validation | That job gets FAILED callback; others proceed |
| LLM call fails entirely | All jobs get FAILED callbacks |
| Gemini timeout | All jobs get FAILED callbacks with `llm_timeout` |
| Schema parse failure + repair fails | All jobs get FAILED callbacks |

---

## 8. DB Persistence

### 8.1 Strategy

`ensure_strategy()` is called **once** using the `brand_intelligence` from the first COMPLETED enrichment. All jobs in the batch share the same strategy.

### 8.2 Jobs

One `jobs` row per valid job in the batch:

| Field | Value |
|-------|-------|
| `user_id` | From envelope `context.account_uuid` |
| `raw_brief_id` | Shared — one raw_brief for the entire envelope |
| `strategy_id` | Shared — one strategy per user |
| `action_code` | `"subscription_strategy"` |
| `orchestrator_agent` | `"job-router"` (default) |
| `input` | Distilled: `{action_code, router_task_id, user_request: job.description, job_index, ...}` |
| `output` | Individual `CallbackBody` for this job |
| `status` | `"done"` or `"failed"` |
| `latency_ms` | Shared LLM latency (same for all jobs in batch) |

### 8.3 Raw Brief

One `raw_briefs` row for the entire envelope (same as single-job). Status:
- `"done"` if at least one job succeeded
- `"failed"` if all jobs failed

---

## 9. Testing

### 9.1 Fixture

**File:** `tests/fixtures/envelopes/subscription_strategy.json`

Based on `nubiex_golden_input.json` with:
- `action_code: "subscription_strategy"`
- `client_request.jobs` array with 3 items (post, story, carousel)
- Same brief, context, and gates as golden input

### 9.2 Unit Tests

1. **Normalizer**: Parse `subscription_jobs` from fixture, verify filtering of invalid `action_key`
2. **Schema**: `MultiEnrichmentOutput.model_validate()` with valid and invalid payloads
3. **Persistence**: Mock DB — verify N jobs created with `orchestrator_agent`

### 9.3 Smoke Test

```bash
MARKETER_RUN_LIVE=1 python scripts/ops/db_e2e_smoke.py --action subscription_strategy
```

Uses `subscription_strategy.json` fixture → real Gemini → Postgres → inspector.

---

## 10. Future Extensions

- **Per-job attachments**: Each job could carry its own `attachments[]`
- **Per-job context overrides**: Different `platform` or `post_id` per job
- **Parallel callbacks**: Dispatch N PATCHes concurrently instead of sequentially
- **Dependency graph**: Jobs that reference each other (e.g., "story teasing the carousel")
- **Calendar awareness**: Date/time targets per job for scheduling coherence
