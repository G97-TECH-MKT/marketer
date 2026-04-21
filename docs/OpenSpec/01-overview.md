# 01 — System Overview

**Version:** 2.0  
**Last Updated:** 2026-04-21

---

## 1. Purpose

**Marketer** is a single-responsibility AI microservice within the Plinng content pipeline. Its sole job is **marketing enrichment**: given a raw task from the ROUTER orchestrator (brand brief, gallery, user request), produce a structured `PostEnrichment v2.0` object that CONTENT_FACTORY can use directly to generate Instagram posts.

It does not:
- Store published content
- Manage brand profiles (reads them)
- Schedule or publish posts
- Make HTTP decisions on behalf of the user

---

## 2. Position in the Pipeline

```
User Request
     │
     ▼
  ROUTER (orchestrator)
     │ POST /tasks
     ▼
  MARKETER  ◄── brief (gate)
     │       ◄── image catalog (gate)
     │
     │  PATCH callback_url
     ▼
  ROUTER
     │
     ▼
  CONTENT_FACTORY  ◄── PostEnrichment v2.0
     │
     ▼
  Published Content
```

The ROUTER dispatches work to Marketer as one step in a multi-agent `agent_sequence`. Marketer is always `step_code: "marketing_enrichment"` and typically `step_order: 1`.

---

## 3. System Architecture

### 3.1 Async ACK Pattern

Marketer uses an **async ACK + callback** pattern, not synchronous request-response. This is a deliberate architectural decision:

1. `POST /tasks` → validates envelope → enqueues background task → returns **202 Accepted** in ~300ms
2. Background worker runs the full pipeline (~12s)
3. `PATCH callback_url` delivers the result to ROUTER

**Why?** The Gemini LLM call takes 8–15 seconds. A synchronous API would force ROUTER to hold open an HTTP connection for that duration, creating timeouts and blocking behavior. The async pattern decouples scheduling from execution.

### 3.2 Processing Pipeline

```
POST /tasks
    │
    ├─ Validate headers (auth, content-type)
    ├─ Validate envelope structure (task_id, callback_url, action_code)
    ├─ Enqueue background_task(process_task, envelope)
    └─ Return 202 {"status": "ACCEPTED", "task_id": "..."}

background_task:
    │
    ├─ 1. NORMALIZE   (normalizer.py)
    │      RouterEnvelope → InternalContext
    │      Extract: brief, gallery, brand_tokens, available_channels, brief_facts
    │      Emit warnings for missing/weak inputs
    │
    ├─ 2. GATE CHECKS  (reasoner.py)
    │      create_web/edit_web → FAILED (not supported)
    │      edit_post without prior_post → FAILED
    │
    ├─ 3. BUILD PROMPT  (reasoner.py + llm/prompts/)
    │      system_prompt + action_overlay + serialized InternalContext
    │
    ├─ 4. LLM CALL  (llm/gemini.py)
    │      Gemini structured output → PostEnrichment (or None on failure)
    │      Temperature: 0.4, max_tokens: 8192, timeout: LLM_TIMEOUT_SECONDS
    │
    ├─ 5. REPAIR  (optional, llm/gemini.py)
    │      If schema validation fails: 1 retry at temperature 0.2
    │      Add warning "schema_repair_used" if successful
    │
    ├─ 6. VALIDATE  (validator.py)
    │      Deterministic checks: URL containment, hallucination guards, CTA coherence
    │      Corrections applied in-place; blocking errors → FAILED
    │
    └─ 7. CALLBACK  (reasoner.py)
           PATCH callback_url with CallbackBody
           Retry policy: 2 attempts, exponential backoff (1s, 2s, 4s)
```

### 3.3 Module Responsibilities

| Module | Lines | Role |
|--------|-------|------|
| `main.py` | 211 | FastAPI app, route handlers, startup/shutdown |
| `config.py` | 26 | Pydantic settings, env var parsing |
| `reasoner.py` | 208 | Pipeline orchestration, callback dispatch |
| `normalizer.py` | 724 | Envelope parsing, context extraction |
| `validator.py` | 448 | Post-LLM deterministic checks |
| `schemas/envelope.py` | 25 | RouterEnvelope (lenient) |
| `schemas/internal_context.py` | ~200 | InternalContext, BrandTokens, etc. |
| `schemas/enrichment.py` | ~300 | PostEnrichment v2.0 (public) |
| `llm/gemini.py` | 99 | Google Gemini wrapper |
| `llm/prompts/system.py` | ~200 | System prompt |
| `llm/prompts/create_post.py` | 43 | create_post action overlay |
| `llm/prompts/edit_post.py` | ~43 | edit_post action overlay |
| `llm/prompts/repair.py` | ~20 | Schema repair prompt |

---

## 4. Technology Choices

### 4.1 FastAPI + asyncio

**Chosen because:**
- Native async I/O — background tasks run concurrently without blocking event loop
- Pydantic v2 integration — automatic request/response validation
- Fast startup (<1s) — important for ECS Fargate cold starts
- `BackgroundTasks` built-in — no external queue needed for MVP

**Concurrency model:**
- FastAPI handles HTTP via uvicorn (async ASGI)
- Each task enqueues a `BackgroundTask` coroutine
- The heavy `reason()` call uses `asyncio.to_thread()` to avoid blocking the event loop
- ~10–20 concurrent tasks per replica (threadpool-bound)

### 4.2 Google Gemini (google-genai SDK)

**Chosen because:**
- Structured output enforcement — Gemini can return JSON conforming to a Pydantic schema
- Cost efficiency — Flash models at ~$0.001–0.002/request
- Quality — sufficient for structured marketing reasoning tasks

**Current model:** `gemini-2.5-flash-preview` (configurable via `GEMINI_MODEL`)

**Structured output flow:**
1. Pass `PostEnrichment` Pydantic schema as `response_schema`
2. Gemini returns JSON conforming to schema
3. If parsing fails: fallback to manual JSON extraction → repair prompt

### 4.3 Pydantic v2

**Chosen because:**
- Type-safe schemas with rich validation rules
- `extra="allow"` on RouterEnvelope for forward-compatibility
- Efficient JSON serialization for prompt building
- Native FastAPI integration

### 4.4 httpx (async)

**Chosen for callback dispatch:**
- Native async HTTP client
- Built-in timeout control
- Retry implemented manually (configurable, with exponential backoff)

---

## 5. Design Decisions & Rationale

### ADR-001: Async ACK + Callback (not sync)

**Decision:** POST /tasks returns 202; result delivered via PATCH callback.

**Rationale:** Gemini latency (8–15s) is incompatible with synchronous HTTP. The async pattern lets ROUTER schedule without blocking. ROUTER's retry mechanism covers lost callbacks (graceful shutdown, container restart).

**Trade-off:** Callback delivery can fail (network, ROUTER downtime). Retry policy (2 attempts, backoff) mitigates but does not eliminate. Future improvement: persistent callback queue in DB.

### ADR-002: Warnings-Only Validator (no blocking checks)

**Decision:** Validator corrects in-place and emits warnings; only catastrophic failures (schema unparseable after repair) result in FAILED status.

**Rationale:** Partial enrichments with warnings are more useful than hard failures. ROUTER/CF can evaluate `degraded=true` and decide policy (retry, fallback, proceed).

**Trade-off:** CONTENT_FACTORY receives degraded enrichments; must handle `degraded=true` defensively.

### ADR-003: Single LLM Pass + 1 Repair

**Decision:** One Gemini call for enrichment; one repair call if schema fails.

**Rationale:** Multi-pass reasoning adds 5–10s per hop. For structured output with clear schema, a single well-engineered prompt + repair is sufficient. Self-critique and multi-agent pass-back are future capabilities.

**Trade-off:** Complex edge cases (unusual brand, multi-language) may produce weaker enrichments than a self-critique loop would.

### ADR-004: Posts-Only MVP (web gated)

**Decision:** `create_web` and `edit_web` return FAILED with `web_not_supported_in_this_iteration`.

**Rationale:** Web content generation requires ATLAS integration (not ready). All code is in place (overlays, schemas); enabling is a config change, not a code change.

**Trade-off:** Web clients receive FAILED responses; must route around Marketer until ATLAS is integrated.

### ADR-005: Lenient Envelope Parsing (`extra="allow"`)

**Decision:** RouterEnvelope accepts unknown fields silently.

**Rationale:** ROUTER evolves faster than Marketer. New fields added by ROUTER should not break Marketer. Only the fields Marketer explicitly consumes are validated; the rest pass through.

**Trade-off:** Malformed payloads inside known fields (e.g., `payload.context`) may cause runtime errors; these are caught and returned as FAILED callbacks.

### ADR-006: Brand Anchors as LLM Constraints

**Decision:** `BrandTokens`, `AvailableChannels`, and `BriefFacts` are extracted deterministically and passed as explicit constraints to the LLM. The validator then enforces them post-generation.

**Rationale:** LLMs hallucinate contact info, URLs, hex codes. Extracting ground truth from the brief and constraining the LLM ("compose around, never invent") eliminates the most harmful hallucinations. The validator scrubs any remaining violations.

**Trade-off:** If the brief itself contains wrong contact info, Marketer will propagate it. The source of truth is the brief; fixing data quality is upstream.

---

## 6. Failure Modes & Recovery

### 6.1 Sync Failures (immediate, before 202)

These are caught before any background work starts:

| Failure | Code | Recovery |
|---------|------|----------|
| Malformed JSON | 400 | Fix payload; ROUTER retries |
| Missing task_id / callback_url | 400 | Fix payload |
| Invalid auth token | 401 | Rotate INBOUND_TOKEN |
| GEMINI_API_KEY missing | 503 | Set env var; restart container |

### 6.2 Async Failures (202 returned, FAILED in callback)

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Unsupported action_code | FAILED + error_message | ROUTER routes elsewhere |
| Web action requested | FAILED (gated) | Wait for ATLAS integration |
| edit_post without prior_post | FAILED + error_message | ROUTER ensures prior_post |
| Gemini timeout | FAILED | Increase LLM_TIMEOUT_SECONDS; check quota |
| Schema validation → repair fails | FAILED | Review prompt; likely model regression |
| Unhandled exception | FAILED | Check logs; fix bug |

### 6.3 Callback Delivery Failures

| Failure | Behavior | Recovery |
|---------|----------|----------|
| ROUTER 5xx | Retry (2x, backoff) | ROUTER recovers; callback succeeds |
| ROUTER 4xx terminal | Log + abandon | Check callback_url; check ORCH_CALLBACK_API_KEY |
| ROUTER unreachable | Retry until exhausted | Log `callback_failed_after_N_attempts` |
| Container restart mid-task | Task lost | ROUTER timeout → retry entire job |

---

## 7. Performance Profile

| Metric | Value |
|--------|-------|
| ACK latency (POST → 202) | p50: 300ms, p95: 500ms |
| End-to-end (202 → PATCH) | p50: 12s, p95: 18s, p99: <30s |
| CPU per task | ~0.05 vCPU (mostly waiting on Gemini) |
| RAM per replica | ~80–120 MB typical |
| Token usage per request | ~3,000–5,000 total |
| Cost per request | ~$0.001–0.003 (Gemini Flash) |
| Concurrent tasks per replica | ~10–20 |

---

## 8. Current Limitations

1. **No persistence**: No audit log, no retry queue. Lost callbacks are unrecoverable without ROUTER timeout+retry.
2. **No multimodal**: Gallery URLs are passed as metadata (tags, dimensions); Gemini does not see actual image bytes.
3. **No prompt caching**: Same brand brief is re-encoded each call. Future optimization with Gemini caching could save 30–40% latency/cost.
4. **Web surface blocked**: `create_web` / `edit_web` return FAILED pending ATLAS.
5. **No client memory**: Each request is stateless; no awareness of previously approved angles or styles.
6. **Single Gemini call**: No self-critique pass; semantic errors not caught by validator may pass through.
