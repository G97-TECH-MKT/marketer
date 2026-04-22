# SPEC — Marketer Agent (v2, operational)

> **Rol:** contrato técnico autoritativo para el equipo que despliega/integra marketer.
> **Estado del código:** v2, posts-only. `schema_version="2.0"`.
> **Última revisión:** 2026-04-21. Reemplaza versión previa (v1 shape).
> **Documentos ligados:**
> - `PRD.md` — producto + scope MVP
> - `docs/ROUTER CONTRACT.md` — contrato de orquestador (fuente de verdad para envelope/callback)
> - `docs/ADR PAYLOAD.md` — evolución v1→v2 del payload del router

---

## 1. Arquitectura

```
ROUTER (orquestador)
  -> POST {marketer_url}/tasks  [Authorization: Bearer <INBOUND_TOKEN>]
  -> marketer valida + encola en background
  -> responde 202 ACCEPTED en <500ms (cuerpo: {task_id, status})
  <- marketer corre reason() en threadpool (~12s)
  <- marketer PATCH <callback_url> [X-API-Key: <ORCH_CALLBACK_API_KEY>]
     body: CallbackBody { status, output_data.enrichment, warnings, trace }
  router actualiza job + dispatcha siguiente step (CONTENT_FACTORY)
```

**Propiedades clave:**
- Async por diseño. ACK 202 inmediato; resultado asíncrono vía PATCH.
- **Persistencia activa**: cada run se guarda en `jobs`; brand intelligence en `strategies`; catalog de actions en `action_types`. Schema autoritativo: `alembic/versions/001_initial_schema.py` (5 tablas: `users`, `action_types`, `raw_briefs`, `strategies`, `jobs`). Un restart pierde tasks en-vuelo (sin queue durable); ROUTER retry cubre.
- Outbound: Gemini + callback_url al router + PostgreSQL (pool interno).
- Stack: Python 3.11+, FastAPI, Pydantic v2, `google-genai` SDK, httpx async para callback, SQLAlchemy 2 async + asyncpg + Alembic para persistencia.

---

## 2. Módulos (layout real)

```
src/marketer/
  main.py                    # FastAPI app: /tasks (202), /tasks/sync (dev), /health, /ready
  config.py                  # Settings (pydantic-settings) desde .env
  reasoner.py                # pipeline: normalize → prompt → Gemini → validate → callback_body
  normalizer.py              # RouterEnvelope → InternalContext (pure)
  validator.py               # chequeos determinísticos post-LLM
  llm/
    gemini.py                # wrapper google-genai con structured output + repair
    prompts/
      system.py              # prompt del sistema (v2)
      create_post.py         # overlay
      edit_post.py           # overlay
      create_web.py          # overlay (gated off en reasoner)
      edit_web.py            # overlay (gated off)
      repair.py              # prompt de repair tras Pydantic fail
  schemas/
    envelope.py              # RouterEnvelope (extra="allow")
    internal_context.py      # InternalContext + FlatBrief + GalleryItem + ...
    enrichment.py            # PostEnrichment + BrandIntelligence + CallbackBody + ...
  db/
    engine.py                # SQLAlchemy async engine + session_scope
    models.py                # ORM: User, ActionType, RawBrief, Strategy, Job
    actions_cache.py         # In-memory TTL cache for action_types catalog (60s)
  persistence.py             # persist_on_ingest / persist_on_complete wiring
alembic/
  versions/
    001_initial_schema.py    # 5 tables: users, action_types, raw_briefs, strategies, jobs
tests/
  test_normalizer.py         # 14 offline
  test_validator.py          # 10 offline
  test_main_async.py         # 12 offline (TestClient + mocks)
  test_golden_casa_maruja.py # 26 live (MARKETER_RUN_LIVE=1)
tests/fixtures/envelopes/    # 10 fixtures cubriendo briefs ricos/pobres/ausentes (post only; web en docs/archive/legacy/)
tests/golden/posts/          # 3 baselines v2 (casa_maruja, minimal, missing_brief)
scripts/
  ops/smoke_async_roundtrip.py   # E2E real con uvicorn + mock callback
  dev/batch_test.py              # 3 verticales × 3 runs → reporte markdown
  demo/build_multi_demo_html.py  # genera docs/examples/runs/marketer_demo_v2.html
  dev/run_fixture.py             # dispara un fixture a /tasks/sync
```

---

## 3. Contrato inbound (ROUTER → marketer)

### 3.1 Endpoint

`POST /tasks` — único ingress de router. Devuelve `202 Accepted`.

`POST /tasks/sync` — dev-only. Ejecuta sync y devuelve `CallbackBody` en el body. NO usado por router.

`GET /health` → `{"status":"healthy"}`
`GET /ready` → `{"status":"ready"}` si `GEMINI_API_KEY` está set; sino `unhealthy`.

### 3.2 Headers que marketer espera

Per `docs/ROUTER CONTRACT.md §3`:

| Header | Requerido | Validación |
|---|---|---|
| `Content-Type: application/json` | sí | implícito |
| `Authorization: Bearer <INBOUND_TOKEN>` | sí si `INBOUND_TOKEN` env está set | 401 si mismatch; si env vacío, se desactiva check (dev) |
| `X-Task-Id` | no | ignorado (redundante con body) |
| `X-Correlation-Id` | no | se propaga en logs y en el PATCH callback |
| `X-Callback-Url` | no | ignorado (se lee de body) |

### 3.3 Body (envelope)

El envelope es el que define `docs/ROUTER CONTRACT.md §3`. Marketer parsea con `extra="allow"` (Pydantic): campos desconocidos pasan sin romper.

**Campos requeridos (400 sync si faltan):**

- `task_id: str`
- `action_code: str`
- `callback_url: str`
- `payload.client_request.description: str` (no vacío)

**Campos opcionales que se usan:**

- `job_id`, `action_id`, `correlation_id`
- `payload.context.{account_uuid, client_name, platform, post_id, website_id, section_id}`
- `payload.action_execution_gates.brief` — ver `docs/BRIEF RESPONSE API.md`
- `payload.action_execution_gates.image_catalog` (o cualquier gate cuya `response.data` sea lista de imágenes)
- `payload.agent_sequence.current.{step_code, agent_id, ...}`
- `payload.agent_sequence.previous[*]` — output de steps anteriores (v2)
- `payload.images[]` (futuro; marketer ya lo lee si router lo añade)

**Ejemplo mínimo `create_post`:**

```json
{
  "task_id": "a84af575-...",
  "job_id": "0d75ec10-...",
  "action_code": "create_post",
  "callback_url": "https://router.internal/api/v1/tasks/a84af575-.../callback",
  "correlation_id": "ig-20260420-007",
  "payload": {
    "client_request": {
      "description": "Crea un post sobre el plato estrella de la semana.",
      "attachments": []
    },
    "context": {
      "account_uuid": "9b1c0f12-...",
      "client_name": "Casa Maruja",
      "platform": "instagram"
    },
    "action_execution_gates": {
      "brief": { "passed": true, "status_code": 200, "response": { "data": { ... brief ... } } },
      "image_catalog": { "passed": true, "status_code": 200, "response": { "data": [ ... items ... ] } }
    },
    "agent_sequence": {
      "current": { "step_code": "marketing_enrichment", "step_order": 1 },
      "previous": {}
    }
  }
}
```

### 3.4 Action codes soportados

| action_code | surface | mode | Estado |
|---|---|---|---|
| `create_post` | post | create | ✅ soportado |
| `edit_post` | post | edit | ✅ soportado (requiere `prior_post` en context o en `agent_sequence.previous`) |
| `create_web` | web | create | ❌ bloqueado por candado en `reasoner.py:96-100` (MVP posts-only) |
| `edit_web` | web | edit | ❌ bloqueado |

Cualquier otro action_code → `FAILED` con `error_message="unsupported action_code: <code>"`.

### 3.5 ACK response (202)

```json
{ "status": "ACCEPTED", "task_id": "a84af575-..." }
```

p95 < 500ms objetivo. Latencia medida típica: ~300ms (smoke test E2E: 294ms).

### 3.6 Respuestas de error sync

| HTTP | Cuándo |
|---|---|
| 400 | Body no es JSON / no es objeto / falta `task_id` / falta `callback_url` |
| 401 | `INBOUND_TOKEN` env set y `Authorization` header no matchea |
| 503 | `GEMINI_API_KEY` no configurado (refuse a aceptar trabajo) |

---

## 4. Contrato outbound (marketer → ROUTER)

### 4.1 Callback

`PATCH {callback_url}` (exactamente la URL del envelope).

### 4.2 Headers salientes

| Header | Valor |
|---|---|
| `Content-Type: application/json` | fijo |
| `X-API-Key: <ORCH_CALLBACK_API_KEY>` | **per-agent API key** que ROUTER asigna durante el registro. NO es el orchestrator shared key; es específico de marketer. |
| `X-Correlation-Id` | propagado del envelope si vino |

### 4.3 Body (`CallbackBody`)

```json
{
  "status": "COMPLETED | FAILED",
  "output_data": {
    "enrichment": { ...PostEnrichment v2.0... },
    "warnings": [ {code, message, field?}, ... ],
    "trace": {
      "task_id": "string",
      "action_code": "string",
      "surface": "post | web",
      "mode": "create | edit",
      "latency_ms": 0,
      "gemini_model": "gemini-3-flash-preview",
      "repair_attempted": false,
      "degraded": false,
      "gallery_stats": { "raw_count": 4, "accepted_count": 4, "rejected_count": 0, "truncated": false }
    }
  },
  "error_message": null
}
```

En `FAILED`: `output_data = null`, `error_message` filled, no `trace`.

### 4.4 Retry del callback

`CALLBACK_RETRY_ATTEMPTS` (default 2) intentos totales con backoff exponencial (1s, 2s, 4s, cap 8s).

- **HTTP 2xx** → success.
- **HTTP 4xx** (excepto 408/429) → no retry, log error. 404/409/422 son terminales per ROUTER CONTRACT §4.
- **HTTP 5xx, timeout, connection error** → retry.
- Tras agotar intentos → log `callback_failed_after_N_attempts`. El ROUTER redispatch handle la falla (su retry level).

Timeout por intento: `CALLBACK_HTTP_TIMEOUT_SECONDS` (default 30s).

---

## 5. PostEnrichment v2 — contrato de output

Pydantic: `src/marketer/schemas/enrichment.py::PostEnrichment`.

### 5.1 Top-level fields

```python
class PostEnrichment(BaseModel):
    schema_version: Literal["2.0"] = "2.0"          # fijo
    surface_format: Literal["post","story","reel","carousel"] = "post"
    content_pillar: Literal["product","behind_the_scenes","customer","education","promotion","community"]
    title: str                                       # interno, consola
    objective: str                                   # interno, business outcome 1-liner
    brand_dna: str                                   # PÚBLICO → CF como client_dna, 200-400 palabras
    strategic_decisions: StrategicDecisions          # {surface_format, angle, voice} cada uno con chosen/alts/rationale
    visual_style_notes: str                          # público, cues de paleta/luz/encuadre
    narrative_connection: str | None                 # null si standalone
    image: ImageBrief                                # {concept, generation_prompt, alt_text}
    caption: CaptionParts                            # {hook, body, cta_line}
    cta: CallToAction                                # {channel, url_or_handle, label}
    hashtag_strategy: HashtagStrategy                # {intent, suggested_volume, themes, tags}
    do_not: list[str]                                # max 5
    visual_selection: VisualSelection                # {recommended_asset_urls, recommended_reference_urls, avoid_asset_urls}
    confidence: Confidence                           # {surface_format, angle, palette_match, cta_channel}: high|medium|low
    brand_intelligence: BrandIntelligence            # INTERNO, 8 campos
    cf_post_brief: str                               # PÚBLICO → instrucción ready-to-execute para CF
```

### 5.2 `brand_dna` — Design-system reference document

**Público. Viaja a CONTENT_FACTORY como `client_dna`.** Texto plano, 200-400 palabras, estructura fija:

```
CLIENT DNA
(Header: business_name + tagline de una línea)

Colors
- #HEX1 · role (e.g. "primary", "accent") · nombre evocador ("tierra cálida")
- #HEX2 · ...

Design Style
(JSON style_reference_analysis block con keys como: mood, photography_direction, composition_style, texture_preference, ...)

Typography
(Estilo tipográfico del brief: FIELD_FONT_STYLE si está; atributos de carácter: sans/serif, weight, personality)

Logo
(Reglas de uso del logo según el brief, si aplica)

Contact
(Una línea compacta: dirección · teléfono · web · email — solo los que vienen en brief_facts)
```

Hexes literales desde `brand_tokens.palette`. Nada se inventa. Si un campo no está en el brief, se omite (el prompt prohíbe rellenar).

### 5.3 `brand_intelligence` — Capa interna

**Interno, no va a CF.** Informa decisiones del agente y alimenta subagentes futuros.

```python
class BrandIntelligence(BaseModel):
    business_taxonomy: str           # snake_case 2-4 tokens: "local_food_service", "b2c_ecom_fashion", ...
    funnel_stage_target: Literal["awareness","consideration","conversion","retention","advocacy"]
    voice_register: str              # 2-5 words, richer than friendly/professional
    emotional_beat: str              # 1-2 words: "pertenencia", "curiosidad", "orgullo_local", ...
    audience_persona: str            # 1-2 sentences with archetype + objection
    unfair_advantage: str            # 1 sentence; "dato insuficiente en el brief" si brief débil
    risk_flags: list[str]            # ["health_disclaimer_needed", "financial_advice", ...] o []
    rhetorical_device: str           # "contraste" | "especificidad_concreta" | "analogía" | "narración_origen" | ...
```

### 5.4 `cf_post_brief` — Ready-to-execute para CF

**Público. Es la instrucción compacta que diseñador+copywriter leen primero.** Texto plano con 3 bloques:

1. **Editorial image note**: 1-3 líneas empezando con "El hook es..." — explica concepto visual y POR QUÉ funciona para esta marca.
2. **`Caption:`** block: `caption.hook + caption.body + caption.cta_line` concatenados verbatim.
3. **`Hashtags:`** block: `hashtag_strategy.tags` unidos por espacios.

Se compone **último** en el prompt, después de todos los otros campos, para que sea coherente con ellos.

### 5.5 Otros sub-schemas

**`CallToAction`:**
- `channel ∈ {website, instagram_profile, facebook, tiktok, linkedin, phone, whatsapp, email, dm, link_sticker, none}`
- `url_or_handle: str | None` — obligatorio para `{website, phone, whatsapp, email, link_sticker}`; null para `{dm, none}`; handle para `instagram_profile`.
- `label: str` — copy del botón en el idioma del brief.
- Validador rechaza channels no presentes en `InternalContext.available_channels` (fuerza a `none`).

**`HashtagStrategy.tags`**: lista de strings con `#` prefix, 5-10 items, el LLM los genera basándose en `intent` y `themes`.

**`VisualSelection`**: validator enforza que `recommended_asset_urls` sea subconjunto del gallery sanitizado; imágenes `role=reference` nunca van a `recommended_asset_urls`.

**`Confidence`**: todos los campos default a `"medium"`.

---

## 6. Normalizer (envelope → InternalContext)

`src/marketer/normalizer.py` — función pura sin I/O.

### 6.1 Pasos

1. Parsear envelope vía `RouterEnvelope` pydantic (lenient).
2. Clean `client_request.description` → `user_request`. Vacío → `ValueError` → `FAILED` callback.
3. Parsear `action_code` → `(surface, mode)`.
4. Extraer brief de `action_execution_gates.brief.response.data`. Normalizar a `FlatBrief` con campos tipados + `extras: dict[str,Any]` para todo lo demás. Coalesce multi-source (top-level brief vs `form_values`).
5. Extraer gallery:
   - De `action_execution_gates.<any_image_catalog-like>.response.data`
   - De `client_request.attachments`
   - De `brief.form_values.FIELD_BRAND_MATERIAL` (role=`brand_asset`)
   - De `payload.images` (reserva futura)
   - Sanitize: http(s), extensions ∈ {jpg,jpeg,png,webp}, size <20MB, dedup por URL, cap 20.
6. Extraer `brand_tokens`, `available_channels`, `brief_facts` (URLs/phones/emails/prices/hex_colors). Son anchors anti-alucinación.
7. Detectar `requested_surface_format` del user_request con regex (story/reel/carousel/post simple).
8. Detectar `prior_post` si `mode=edit`.
9. Retornar `(InternalContext, warnings[])`.

### 6.2 Policy

- **Empty-sentinels** ("ninguno", "none", "n/a", "-") → `None`.
- **Lenient**: campos desconocidos pasan por `extras`. Nunca rechazar por shape.
- **Brief/request reconciliation**: si `brief.brief_background` divergen de `client_request.description` (overlap <25% tokens >4 chars) → warning `brief_request_mismatch`, live request gana.
- **Warnings no-bloqueantes**: gallery_empty, brief_missing, tone_unclear, request_vague, context_missing_id, brief_field_missing, value_proposition_empty.

---

## 7. Reasoner (pipeline)

`src/marketer/reasoner.py`.

```
reason(envelope_dict, gemini, extras_truncation) -> CallbackBody:
    1. normalize() → (ctx, normalizer_warnings)
         - ValueError → FAILED "unsupported_action_code" o "client_request.description required"
    2. if ctx.surface == "web" → FAILED "web_not_supported_in_this_iteration"
    3. if action_code == "edit_post" and prior_post is None → FAILED "prior_post_missing"
    4. Build prompt: SYSTEM + overlay_for_action + serialized_context
    5. gemini.generate_structured(prompt) → (enrichment, raw_text, err)
         - if enrichment is None → one repair attempt with error + raw_text
         - if still None → FAILED "schema_validation_failed"
         - emits warning schema_repair_used if repair succeeded
    6. validate_and_correct(enrichment, ctx) → (enrichment, validator_warnings, blocking_errors)
         - blocking_errors: none in v2 (everything is warning-level except schema parse)
    7. Assemble CallbackBody(status=COMPLETED, output_data=CallbackOutputData(enrichment, warnings, trace))
         - trace.degraded = any warning in {brief_missing, gallery_empty, gallery_all_filtered}
```

Timeout soft: no hay timeout explícito en reason(); lo controla el timeout del cliente Gemini (`LLM_TIMEOUT_SECONDS`, default 30s).

Latencia medida: p50 ~12s, p95 ~18s (monopaso, sin caching).

---

## 8. Validator (determinístico)

`src/marketer/validator.py::validate_and_correct(enrichment, ctx) → (enrichment, warnings, blocking)`.

### 8.1 Correcciones automáticas

1. **Surface format override**: si `ctx.requested_surface_format` está set y difiere del LLM → forzar + warning `surface_format_overridden`.
2. **Visual selection URLs**: `recommended_asset_urls` filtradas para que ⊆ gallery sanitized → warning `visual_hallucinated`.
3. **Role=reference en assets**: se mueven a `recommended_reference_urls` → warning `reference_used_as_asset`.
4. **URL dedup** entre asset y reference lists.
5. **Hallucination guards** en campos de texto (`brand_dna`, `visual_style_notes`, `image.concept/prompt/alt_text`, `caption.hook/body/cta_line`):
   - URLs no en `brief_facts.urls` → scrub + warning `claim_not_in_brief`
   - Hex fuera de `brand_tokens.palette` → scrub + warning `palette_mismatch`
   - Emails no en `brief_facts.emails` → scrub + warning `claim_not_in_brief`
   - Phones no en `brief_facts.phones` → scrub + warning `claim_not_in_brief`
   - Prices no en `brief_facts.prices` → warning `price_not_in_brief` (no scrub — muy agresivo)
6. **CTA channel validation**: si `cta.channel` no está en `available_channels` → set a `none` + warning `cta_channel_invalid`. Si `cta.url_or_handle` no matchea → misma acción. `cta.channel ∈ {dm, link_sticker, none}` siempre limpia `url_or_handle`.
7. **CTA/caption coherence**: si `caption.cta_line` menciona un canal distinto al elegido → warning `cta_caption_channel_mismatch`. Si `cta.channel=="none"` y cta_line menciona cualquier canal → mismo warning.
8. **Caption length caps** (warnings si exceden): post 125/1900/180/2200; story 80/220/80/250; reel 100/850/150/1000; carousel como post.
9. **`do_not` cap a 5 items** → warning `do_not_truncated`.

### 8.2 Warnings no-bloqueantes adicionales

- `field_missing` si `title`, `objective`, `image.concept`, `caption.hook`, `caption.body` vacíos.

### 8.3 Blocking errors

En v2: ninguno. El schema Pydantic ya garantiza required. Repair se intenta solo si Pydantic falla en parse inicial. El `blocking` list se retorna vacío en v2 — queda el hook disponible para futuras reglas que sí quieran bloquear.

---

## 9. Auth

Per `docs/ROUTER CONTRACT.md §3, §4, §10`:

### 9.1 Inbound (ROUTER → marketer)

- Header: `Authorization: Bearer <INBOUND_TOKEN>`
- Env var: `INBOUND_TOKEN`
- Comportamiento: si `INBOUND_TOKEN` está set y el header no matchea → 401. Si `INBOUND_TOKEN` vacío (dev) → sin check.
- Este token es el `agents.auth_token` que ROUTER tiene registrado para marketer.

### 9.2 Outbound callback (marketer → ROUTER)

- Header: `X-API-Key: <ORCH_CALLBACK_API_KEY>`
- Env var: `ORCH_CALLBACK_API_KEY`
- Este es el **per-agent API key** asignado por ROUTER durante el registro. No es el orchestrator shared key.

### 9.3 HMAC (opcional)

ROUTER define HMAC en `ROUTER CONTRACT §10` pero no lo enforza hoy. Marketer no implementa HMAC. Cuando router lo active, añadir el header de firma.

---

## 10. Configuration (env vars)

Loaded via `pydantic-settings` desde `.env` (local) o env real (producción). Ver `.env.example` para placeholders.

| Var | Default | Propósito |
|---|---|---|
| `GEMINI_API_KEY` | `""` | Key de Google Gemini API. **Requerida**; sin ella `/ready` devuelve `unhealthy` y `/tasks` devuelve 503. |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Modelo Gemini. |
| `LLM_TIMEOUT_SECONDS` | `30` | Timeout del cliente Gemini. |
| `LOG_LEVEL` | `INFO` | Nivel de log estándar Python. |
| `EXTRAS_LIST_TRUNCATION` | `10` | Cap por lista cuando se serializa `brief.extras` al prompt. Bounded de coste del prompt. |
| `INBOUND_TOKEN` | `""` | Bearer token esperado en `Authorization`. Vacío desactiva auth (solo dev). |
| `ORCH_CALLBACK_API_KEY` | `""` | `X-API-Key` del callback PATCH a ROUTER. Vacío → header no se envía (dev/testing). |
| `CALLBACK_HTTP_TIMEOUT_SECONDS` | `30.0` | Timeout del PATCH callback. |
| `CALLBACK_RETRY_ATTEMPTS` | `2` | Nº de intentos del PATCH (1 inicial + retries). |
| `DATABASE_URL` | `""` | PostgreSQL asyncpg URL (`postgresql+asyncpg://user:pw@host:5432/db`). Vacío → degraded mode (actions hardcoded, sin persistencia, sin memory). |
| `DB_POOL_SIZE` | `10` | Pool de conexiones. |
| `DB_POOL_MAX_OVERFLOW` | `5` | Conexiones extra en burst. |
| `DB_POOL_TIMEOUT_SECONDS` | `10` | Timeout al pedir del pool. |
| `RUNS_RETENTION_DAYS` | `90` | Retención de `marketer_runs`. 0 = sin límite. |
| `CLIENT_MEMORY_TTL_DAYS` | `0` | 0 = memory no expira. |
| `ACTIONS_CACHE_TTL_SECONDS` | `60` | TTL del cache in-memory de actions. |

Dependencia única en DB: PostgreSQL 14+. Sin SQS ni Redis en MVP.

---

## 11. Error handling

| Condición | Respuesta |
|---|---|
| Envelope malformado (faltan required) | sync 400 |
| Auth inbound fail | sync 401 |
| `GEMINI_API_KEY` no set | sync 503 |
| `action_code` unsupported | 202 → FAILED callback `unsupported_action_code` |
| `create_web` / `edit_web` | 202 → FAILED callback `web_not_supported_in_this_iteration` |
| `edit_post` sin `prior_post` | 202 → FAILED callback `prior_post_missing` |
| Brief missing / gallery empty | 202 → COMPLETED, `degraded=true`, warnings |
| Gemini timeout/non-transient | 202 → FAILED callback `internal_error: <type>: <msg>` |
| Schema Pydantic falla tras 1 repair | 202 → FAILED callback `schema_validation_failed: <err>` |
| Excepción no manejada en background | 202 → FAILED callback `internal_error: <type>: <msg>` |
| Callback PATCH 2xx | success |
| Callback PATCH 4xx terminal (404/409/422) | no retry, log error |
| Callback PATCH 5xx / network | retry `CALLBACK_RETRY_ATTEMPTS` veces con backoff exponencial |

### 11.1 Interacción con ROUTER retry

ROUTER redispatcha automáticamente cuando task llega a FAILED/TIMEOUT con `retry_count < max_retries` (backoff 30s/60s/120s...). Marketer **no implementa job-level retry**:

- Si el callback reporta FAILED, ROUTER redispatcha el mismo `task_id`.
- Marketer debe tratar el 2º arrival del mismo `task_id` como intento nuevo (hoy sin task registry — no hay conflicto, siempre procesa).

---

## 12. Observability

### 12.1 Logs

JSON-formatted por default (`format='{"level":"...","logger":"...","msg":...}'`). Cada línea de nivel WARN+ lleva:
- `task_id`
- `correlation_id`
- `attempt` (en retries)
- `error` (tipo + msg, redactado)

No se loguea `brief` completo ni PII a nivel INFO. DEBUG puede.

### 12.2 Traza en el callback

`output_data.trace` contiene `latency_ms`, `repair_attempted`, `degraded`, `gallery_stats`. El router la guarda y es consumible por la consola.

### 12.3 Métricas (futuro)

Cuando se despliegue con Prometheus/Cloudwatch: ver `PRD §10 Success metrics`. No implementado en MVP.

---

## 13. Deployment

### 13.1 Container

Dockerfile recomendado (a crear):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
USER nobody
CMD ["uvicorn", "marketer.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- Single worker por process. Async I/O maneja concurrencia intra-process.
- Horizontal scaling: múltiples replicas.
- Health probe: `GET /health`. Readiness: `GET /ready`.
- Graceful shutdown: SIGTERM — uvicorn drena connections. Tasks en-vuelo pueden perderse; ROUTER retry cubre.

### 13.2 Resources (estimado)

- CPU: 1 vCPU (90% del tiempo esperando Gemini)
- RAM: 512 MB (sobra; Pydantic + FastAPI)
- Concurrent tasks por replica: ~10-20 (limitado por threadpool async que corre `reason()` bloqueante)

### 13.3 Local run

```bash
# Option 1: bare uvicorn
PYTHONPATH=src python -m uvicorn marketer.main:app --port 8000 --reload

# Option 2: con docker
docker build -t marketer .
docker run -p 8080:8080 --env-file .env marketer
```

### 13.4 Smoke test E2E

`scripts/smoke_async_roundtrip.py`: levanta uvicorn + mock callback server, POST envelope, verifica 202 + PATCH recibido + shape del body. Corre en ~15s contra Gemini real.

---

## 14. Integration runbook (ROUTER side)

Este checklist lo ejecuta el equipo router con acceso a su BD.

### 14.1 Pre-requisitos

- Container marketer desplegado y con `endpoint_url` público/internal reachable desde ROUTER.
- Marketer registrado con `GEMINI_API_KEY`, `INBOUND_TOKEN`, `ORCH_CALLBACK_API_KEY` en sus env vars.
- `GET https://<marketer-host>/ready` devuelve 200.

### 14.2 Registro en router

```sql
-- 1) Agente
INSERT INTO agents (id, name, description, endpoint_url, auth_token,
                    max_concurrent_tasks, timeout_seconds, is_active,
                    created_at, updated_at)
VALUES (gen_random_uuid(), 'marketer',
        'Master Marketing Agent — post enrichment',
        'https://marketer.internal.plinng.com',
        '<INBOUND_TOKEN matching marketer env>',
        20, 90, true, now(), now())
ON CONFLICT (name) DO UPDATE
  SET endpoint_url=EXCLUDED.endpoint_url,
      auth_token=EXCLUDED.auth_token,
      is_active=true,
      updated_at=now();

-- 2) Guardar el X-API-Key del agente para callbacks
-- (este es el ORCH_CALLBACK_API_KEY que marketer usa; ROUTER lo valida en PATCH)
-- Mecanismo depende del esquema de router — puede ser una tabla agent_api_keys o similar.

-- 3) Acciones
INSERT INTO action_catalog (id, action_code, display_name, description,
                            agent_id, is_active, created_at, updated_at)
SELECT gen_random_uuid(), 'create_post', 'Create Instagram Post',
       'Genera enrichment para un post nuevo', a.id, true, now(), now()
FROM agents a WHERE a.name='marketer'
ON CONFLICT (action_code) DO UPDATE
  SET agent_id=EXCLUDED.agent_id, is_active=true, updated_at=now();

INSERT INTO action_catalog (id, action_code, display_name, description,
                            agent_id, is_active, created_at, updated_at)
SELECT gen_random_uuid(), 'edit_post', 'Edit Instagram Post',
       'Genera enrichment para editar un post existente', a.id, true, now(), now()
FROM agents a WHERE a.name='marketer'
ON CONFLICT (action_code) DO UPDATE
  SET agent_id=EXCLUDED.agent_id, is_active=true, updated_at=now();

-- 4) Gates (asumir que brief + image_catalog ya existen como servicios)
-- Ver ROUTER CONTRACT §11 para action_execution_gates

-- 5) Secuencia: marketer = step 1, content_factory = step 2
INSERT INTO agent_sequence (action_id, step_code, agent_id, sort_order,
                            is_mandatory, timeout_seconds, output_schema)
SELECT ac.id, 'marketing_enrichment', a.id, 1, true, 90, 'post_enrichment.v2'
FROM action_catalog ac, agents a
WHERE ac.action_code='create_post' AND a.name='marketer';

-- (repetir para edit_post)
-- (step 2 apunta a content_factory con sort_order=2)
```

### 14.3 Smoke E2E desde router

```bash
curl -X POST https://<router-host>/api/v1/jobs \
  -H "X-API-Key: <ORCH_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "create_post",
    "client_request": { "description": "Post sobre el plato estrella." },
    "context": { "account_uuid": "...", "platform": "instagram" },
    "correlation_id": "smoke-001"
  }'

# Esperar webhook o consultar GET /api/v1/jobs/{job_id}
```

Logs a mirar en orden:
1. Router consumer recibe de SQS → crea job.
2. Router dispatcha POST /tasks a marketer → 202 en <500ms.
3. Marketer log: `task_id=X correlation_id=Y`.
4. ~12s después, marketer log: `callback_ok status=200 attempt=1`.
5. Router recibe PATCH, actualiza job, dispatcha CONTENT_FACTORY.
6. CF recibe enrichment, genera post final.

---

## 15. Testing

### 15.1 Offline suite (36 tests, <2s)

```bash
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_golden_casa_maruja.py
```

- `test_normalizer.py` (14): brief flattening, gallery sanitize, anchors, prior_post detection.
- `test_validator.py` (10): hallucination guards, CTA validation, visual selection, caption caps.
- `test_main_async.py` (12): 202 ACK, background callback, auth, sync endpoint, 400/401/503.

### 15.2 Live golden (26 tests, ~20s, 1 Gemini call)

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python -m pytest tests/test_golden_casa_maruja.py -v
```

Corre el pipeline real contra Casa Maruja fixture. Assertions deterministas: `cta.channel=dm`, visual_selection correcta, schema shape, brand_intelligence populated, brand_dna con secciones requeridas, zero warnings.

### 15.3 E2E smoke

```bash
PYTHONPATH=src python scripts/smoke_async_roundtrip.py
```

Verifica el roundtrip HTTP real con uvicorn + mock callback server.

### 15.4 Batch cross-vertical

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/batch_test.py
```

Corre 3 fixtures × 3 runs = 9 calls, reporta `reports/batch_test_<date>.md` con consistencia de decisiones estratégicas entre runs, latencia p50/max, red flags.

---

## 16. MVP cerrado (2026-04-21)

Verificado:

- ✅ POST /tasks 202 en 294ms (smoke)
- ✅ Background PATCH callback en ~12s
- ✅ schema v2.0 con todos los campos nuevos (brand_dna, brand_intelligence, cf_post_brief, hashtag.tags)
- ✅ 62/62 tests verdes
- ✅ Auth inbound (Bearer) + outbound (X-API-Key) implementados con fallback dev
- ✅ Retry del callback con backoff
- ✅ Graceful fail en 5 paths del §11
- ✅ Fixtures cubren briefs ricos/pobres/ausentes; todas degradan correctamente
- ✅ Gallery sanitize + CTA coherence + hallucination guards probados
- ✅ Validator enforza subset del gallery, channels válidos, coherencia cta/caption

Lo que NO está hecho (listado explícito para evitar confusión):

- ❌ Dockerfile y pipeline CI (infra team lo hace)
- ❌ Registro real en router BD (router team lo hace)
- ❌ Observabilidad con Prometheus/Cloudwatch (futuro)
- ❌ Multimodal vision, web search, self-critique, caching brand_profile (futuras iteraciones; ver PRD §12)

---

## 17. Decisiones cerradas (para no re-abrir)

- **Async callback pattern** — ACK 202 + PATCH callback. NO sync.
- **Posts-only MVP** — `create_web`/`edit_web` gated off (ahora via `marketer_actions.is_enabled=false`; previo: hardcoded en `reasoner.py:96-100`).
- **DB-backed persistence + memory** — PostgreSQL dedicada. 3 tablas: `marketer_actions`, `marketer_runs`, `marketer_client_memory`. NFR-8 "purity/no-persistence" del SPEC v1 RETIRADO. Ver `docs/PERSISTENCE.md`.
- **Schema v2.0** — PostEnrichment con `brand_dna`, `brand_intelligence`, `cf_post_brief`, `hashtag_strategy.tags`.
- **Single-shot reasoning** — 1 Gemini call + 1 repair attempt. No sub-agents internos (evita god-agent trap).
- **Gemini 3 Flash Preview** — `gemini-3-flash-preview` via google-genai SDK. `max_output_tokens=8192`.
- **Structured output** — Pydantic response_schema; fallback a parse-from-text.
- **Degradation = warning, never FAILED** — brief missing / gallery empty producen degraded=true, no fallan la task.
- **LLM produces craft in v2** — a diferencia de v1 del PRD, marketer SÍ produce caption publicable + image generation prompt concreto. No más "direction-only".
- **brand_dna es PÚBLICO** (CF lo consume), `brand_intelligence` es INTERNO (para subagentes futuros).
- **Hashtag tags + themes** — tags[] son strings con `#` listos; themes[] siguen siendo dirección conceptual.
- **No HMAC** en callbacks (router no enforza).
- **No persistence** — in-memory only; ROUTER retry cubre pérdida de state.
- **Normalizer es lenient** — `extra="allow"` en envelope, coalesce multi-source, `extras: dict` para fields desconocidos.
- **Validator es determinístico** — no LLM self-critique en MVP.

---

## 18. Open items (bloqueantes al conectar)

1. **INBOUND_TOKEN valor**: acordar con router team qué token usar; mismo valor va a `agents.auth_token` en router BD y a env var de marketer.
2. **ORCH_CALLBACK_API_KEY valor**: lo genera/asigna el router team durante el registro de marketer como agente; lo pasan a marketer para que vaya en el PATCH.
3. **Gate code para image_catalog**: confirmar con router si es literalmente `image_catalog` o varía. Hoy el normalizer detecta heurísticamente cualquier gate cuyo `response.data` sea array de image-like objects.
4. **Endpoint URL**: hostname/puerto donde despliega infra. Ese valor va en `agents.endpoint_url`.
5. **Timeout del step**: recomendar `agent_sequence.current.timeout_seconds=90` (p95 real ~18s deja margen 5x).
6. **output_schema registration**: publicar `post_enrichment.v2`. Router puede activar validación schema-based en callback cuando quiera.
7. **DB provisioning** (nuevo): PostgreSQL dedicada recomendada. Infra team provee `DATABASE_URL`. Migrations con `alembic upgrade head` al deploy. Ver `docs/PERSISTENCE.md §7`.
8. **Decisión shared vs dedicated DB**: ver `docs/PERSISTENCE.md §7.1`. Recomendación: dedicada.
9. **Política de retención de runs**: default 90 días; legal puede pedir 365. `RUNS_RETENTION_DAYS` env var.
10. **GDPR delete por cliente**: `marketer_runs.envelope` contiene PII del brief. Soportar `DELETE FROM marketer_runs WHERE account_uuid=...` + invalidate memory cuando usuario pide borrado. Operacionalizar en admin endpoint post-MVP.
