# PERSISTENCE — marketer DB layer

> **Status:** diseño propuesto. Las tablas, el flujo y el contrato están definidos; el código real (SQLAlchemy + Alembic + repositories) se implementa en un PR dedicado.
> **Motivación:** marketer debe **recordar** a cada cliente. Hoy cada llamada es tabula rasa. Con DB-backed memory:
> - Los `action_codes` soportados son configurables (DB, no código).
> - Cada run queda auditado (`marketer_runs`) para replay y debug.
> - Por cliente (`account_uuid`) se mantiene memory agregada (`marketer_client_memory`) que alimenta los próximos runs: últimos ángulos usados, content pillars recientes, brand_dna cacheado, etc.

---

## 1. Por qué DB

Sin DB, cada POST a marketer es un black box independiente. Consecuencias reales:

1. **Drift de consistencia.** Dos posts consecutivos de Casa Maruja pueden elegir ángulos opuestos sin que el agente lo sepa.
2. **Repetición.** Sin saber qué se publicó antes, el agente puede proponer el mismo ángulo 3 semanas seguidas.
3. **Waste.** `brand_dna` y partes de `brand_intelligence` son estables por cliente (su historia no cambia cada post); regenerarlos en cada call quema tokens y drifta la narrativa.
4. **Sin audit.** Si CF rechaza un post, no podemos replay lo que marketer recibió y produjo.
5. **Sin loop de mejora.** Métricas downstream (post views, engagement) no pueden correlacionarse con decisiones upstream porque no hay un id estable.

**Decisión:** marketer adopta PostgreSQL como su store. NFR-8 "no persistence" del spec v1 queda retirado.

---

## 2. Tablas

### 2.1 `marketer_actions` — acciones configurables

Reemplaza el set hardcoded de `{create_post, edit_post, create_web, edit_web}` que vive hoy en `normalizer.py::_parse_action_code` y `reasoner.py::OVERLAYS`.

```sql
CREATE TABLE marketer_actions (
    action_code         varchar(100) PRIMARY KEY,
    surface             varchar(20)  NOT NULL
        CHECK (surface IN ('post', 'web', 'other')),
    mode                varchar(20)  NOT NULL
        CHECK (mode IN ('create', 'edit', 'other')),
    prompt_overlay      varchar(100) NOT NULL,
        -- e.g. 'create_post' → src/marketer/llm/prompts/create_post.py::CREATE_POST_OVERLAY
    requires_prior_post boolean      NOT NULL DEFAULT false,
    is_enabled          boolean      NOT NULL DEFAULT true,
    notes               text,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now()
);

-- Seed inicial (MVP posts-only)
INSERT INTO marketer_actions
    (action_code, surface, mode, prompt_overlay, requires_prior_post, is_enabled, notes)
VALUES
    ('create_post', 'post', 'create', 'create_post', false, true,  'MVP'),
    ('edit_post',   'post', 'edit',   'edit_post',   true,  true,  'MVP; requires prior_post in envelope'),
    ('create_web',  'web',  'create', 'create_web',  false, false, 'Overlay exists; gated off until ATLAS integration'),
    ('edit_web',    'web',  'edit',   'edit_web',    false, false, 'Overlay exists; gated off');
```

**Semántica:**
- `is_enabled=false` + `action_code` solicitado → `FAILED` callback con `error_message="action_not_enabled"`. Permite apagar una action sin deploy.
- `prompt_overlay` es el **nombre** del archivo overlay en `src/marketer/llm/prompts/`. El overlay es código (tiene lógica específica del action); solo el mapeo action→overlay está en DB.
- `requires_prior_post=true` → el reasoner verifica `prior_post` en el envelope; sin él, `FAILED prior_post_missing`.
- Añadir un `action_code` nuevo: INSERT en la tabla + crear el overlay file + (opcional) seed. Cero cambios en reasoner/validator si el overlay respeta el contrato `PostEnrichment`.

**Cache:** in-memory con TTL 60s (refresh lazy al primer request tras expiración). Un endpoint admin `POST /admin/actions/refresh` fuerza reload si se edita la tabla fuera de banda.

### 2.2 `marketer_runs` — historial append-only por task

Un row por task recibida (incluso las FAILED). Inmutable. Auditoría + replay + feed a `marketer_client_memory`.

```sql
CREATE TABLE marketer_runs (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             uuid        NOT NULL,           -- del envelope
    job_id              uuid        NULL,
    correlation_id      text        NULL,
    account_uuid        uuid        NULL,               -- del payload.context.account_uuid
    action_code         varchar(100) NOT NULL,
    status              varchar(20) NOT NULL
        CHECK (status IN ('COMPLETED', 'FAILED')),
    error_message       text        NULL,

    -- Input capture (for replay / audit)
    envelope            jsonb       NOT NULL,           -- full POST /tasks body

    -- Output capture (null if FAILED without parseable output)
    enrichment          jsonb       NULL,               -- full PostEnrichment v2.0
    warnings            jsonb       NOT NULL DEFAULT '[]',

    -- Trace dimensions (denormalized for fast aggregations)
    surface             varchar(20) NULL,
    mode                varchar(20) NULL,
    gemini_model        varchar(100) NULL,
    latency_ms          integer     NULL,
    repair_attempted    boolean     NOT NULL DEFAULT false,
    degraded            boolean     NOT NULL DEFAULT false,

    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_marketer_runs_account_time ON marketer_runs (account_uuid, created_at DESC);
CREATE INDEX idx_marketer_runs_task ON marketer_runs (task_id);
CREATE INDEX idx_marketer_runs_status ON marketer_runs (status, created_at DESC);
```

**Semántica:**
- **Append-only.** Nunca se hace UPDATE sobre un row de runs. Si la misma `task_id` llega dos veces (ROUTER retry), se inserta una segunda fila; el `status` final es el del row más reciente.
- `envelope` y `enrichment` completos como jsonb. Permite rehacer cualquier run localmente: read envelope → pipe a reason() mockeado → diff contra enrichment guardado.
- Indexado por `(account_uuid, created_at DESC)` porque la query más caliente va a ser "los N runs más recientes de este cliente".

**Retención:** 90 días por default. Rows más viejos se pueden archivar a cold storage o eliminar. Política en env var `RUNS_RETENTION_DAYS`.

### 2.3 `marketer_client_memory` — agregado derivado por cliente

Un row por `account_uuid`. Mantiene el "estado actual" del cliente desde la perspectiva de marketer. Se actualiza tras cada run COMPLETED.

```sql
CREATE TABLE marketer_client_memory (
    account_uuid                uuid         PRIMARY KEY,

    -- Capas estables del brief (reusables entre runs del mismo brief_hash)
    brief_hash                  varchar(64)  NULL,        -- hash del brief para invalidación
    brand_dna_cached            text         NULL,
    brand_intelligence_static   jsonb        NULL,
        -- Subset estable de brand_intelligence:
        -- {business_taxonomy, voice_register, unfair_advantage, audience_persona,
        --  risk_flags}. NO incluye funnel_stage_target, emotional_beat, rhetorical_device
        -- (son post-level, cambian por run).

    -- Historial condensado para alimentar el próximo prompt
    angles_recent               jsonb        NOT NULL DEFAULT '[]',
        -- Array de {angle, pillar, surface_format, date} de los últimos 10 runs COMPLETED.
        -- El prompt lo referencia para evitar repetición y detectar patrones.
    content_pillars_used        jsonb        NOT NULL DEFAULT '{}',
        -- Dict {pillar: {count, last_used_at}} — para diversificación de calendario.
    cta_channel_distribution    jsonb        NOT NULL DEFAULT '{}',
        -- Dict {channel: count} — para ver si hay over-reliance en un solo canal.
    surface_format_distribution jsonb        NOT NULL DEFAULT '{}',
        -- Dict {surface_format: count}.

    -- Metadata
    run_count                   integer      NOT NULL DEFAULT 0,
    last_run_at                 timestamptz  NULL,
    last_run_task_id            uuid         NULL,
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    updated_at                  timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_marketer_client_memory_last_run ON marketer_client_memory (last_run_at DESC);
```

**Semántica:**
- **Refresh atómico tras cada run COMPLETED.** Dentro del mismo commit que inserta en `marketer_runs`, UPDATE de `client_memory` por `account_uuid` (upsert si no existe).
- `brief_hash` es `sha256(canonical_json(brief_form_values))`. Si el brief del cliente cambia, el hash cambia → el `brand_dna_cached` queda stale y debe regenerarse (o el prompt lo usa como contexto pero el LLM lo revisa). Política: invalidar `brand_dna_cached` + `brand_intelligence_static` cuando el hash cambia; el próximo run los regenera.
- `angles_recent` cap a 10 items, rolling. Se añade el nuevo al frente; se drop el más viejo.
- `content_pillars_used` y `cta_channel_distribution` son histograms acumulativos. Si un cliente lleva 30 posts con pillar=product y 2 con education, el prompt puede ver ese desbalance y sugerir diversificación.

**TTL de memory:** sin expirar por default. Row se mantiene mientras el cliente exista. Env var `CLIENT_MEMORY_TTL_DAYS` para purga opcional (ej. clientes inactivos >180 días).

---

## 3. Flujo runtime

```
POST /tasks (envelope)
    │
    ├─ 1. Extract account_uuid del envelope.payload.context.
    │     Si ausente → log warning, pero continuar (memory queda null).
    │
    ├─ 2. SELECT marketer_actions WHERE action_code = envelope.action_code.
    │     - is_enabled=false → FAILED callback "action_not_enabled"
    │     - requires_prior_post=true y sin prior_post → FAILED "prior_post_missing"
    │
    ├─ 3. SELECT marketer_client_memory WHERE account_uuid = <uuid>.
    │     Si existe → cargar en InternalContext.client_memory.
    │     Si no existe → client_memory = None.
    │
    ├─ 4. Normalize envelope → InternalContext (con client_memory inyectado).
    │
    ├─ 5. Build prompt: SYSTEM + overlay + context + client_memory.
    │     El prompt instruye al LLM:
    │     - Si client_memory.angles_recent contiene el ángulo que estás eligiendo,
    │       o es muy similar → escoge otro ángulo y báilalo en el rationale.
    │     - Si content_pillars_used muestra un pillar sobre-usado → preferir otro.
    │     - Si brand_dna_cached está presente y brief_hash matchea el del envelope,
    │       PUEDES reusarlo verbatim (ahorra tokens, mantiene consistencia).
    │     - brand_intelligence_static: reutiliza voice_register, unfair_advantage,
    │       audience_persona tal cual; solo re-deriva los campos post-level.
    │
    ├─ 6. Gemini call → enrichment.
    │
    ├─ 7. validate_and_correct(enrichment, ctx).
    │
    ├─ 8. Transaction:
    │     INSERT INTO marketer_runs (...) VALUES (...);
    │     UPSERT INTO marketer_client_memory (account_uuid, ...)
    │       con los nuevos angles_recent, pillar counts, etc.
    │
    └─ 9. PATCH callback_url con CallbackBody.
```

**Failure paths:**
- DB unreachable al step 2/3: log error, continuar con client_memory=None y actions cargadas del cache/fallback in-memory (degraded mode). No bloquea el request.
- DB unreachable al step 8: log error crítico. El callback se manda igual (CF no se ve afectado) pero la run no queda persistida. Alerta a monitoreo.

---

## 4. Prompt injection — cómo el LLM usa la memory

En el system prompt se añade una sección condicional que solo aparece cuando `client_memory` está poblada:

```
# Client memory (cuando el cliente ya tuvo runs previos)

Esta marca tiene historial con marketer. Tu output debe ser COHERENTE con su
narrativa pasada Y diverso frente a lo reciente. Señales:

- `client_memory.angles_recent` — los últimos 10 ángulos elegidos. NO repitas
  el mismo ángulo que el run anterior. Si detectas repetición, elige un ángulo
  vecino y justifícalo en strategic_decisions.angle.rationale.
- `client_memory.content_pillars_used` — distribución histórica. Si un pillar
  representa >60% del total con >5 runs, diversifica escogiendo otro pillar
  (a menos que el user_request pida ese específicamente).
- `client_memory.brand_dna_cached` + `brief_hash` — si el hash del brief actual
  matchea el cacheado, PUEDES reutilizar brand_dna_cached verbatim. Si
  difiere, regenera.
- `client_memory.brand_intelligence_static` — reutiliza business_taxonomy,
  voice_register, unfair_advantage, audience_persona, risk_flags. Solo
  re-deriva funnel_stage_target, emotional_beat, rhetorical_device (son
  post-level).
```

El formato de `client_memory` en el contexto del prompt es el jsonb compacto, truncado a 10 items donde aplica.

---

## 5. Módulo Python propuesto

```
src/marketer/
  db/
    __init__.py
    engine.py           # SQLAlchemy async engine + session factory
    models.py           # ORM models: MarketerAction, MarketerRun, MarketerClientMemory
    repositories/
      actions.py        # load_action(code) → ActionSpec | None; caches 60s
      runs.py           # insert_run(run) → id
      client_memory.py  # get_memory(account_uuid), upsert_memory_after_run(run)
    migrations/
      alembic.ini
      versions/
        001_initial.py  # create 3 tables + indexes + seed marketer_actions
```

Integración:
- `reasoner.py` recibe un `actions_repo` + `memory_repo` por dependency injection (o vía `app.state`).
- `main.py` crea engine al startup (`@app.on_event("startup")`) y lo inyecta en `app.state`.
- Tests pueden usar `testcontainers-postgres` para integración real, o patch repositories para unit.

Stack:
- **SQLAlchemy 2.x async** con `asyncpg` driver. Ya es el stack FastAPI-native.
- **Alembic** para migrations. Una migration inicial (`001_initial.py`) crea las 3 tablas, los indexes y los seeds.
- `testcontainers-postgres` para tests de integración (opt-in con `MARKETER_RUN_DB_TESTS=1`).

---

## 6. Env vars nuevas

Añadir a `config.py` y `.env.example`:

| Var | Default | Propósito |
|---|---|---|
| `DATABASE_URL` | `""` | PostgreSQL connection string (asyncpg format: `postgresql+asyncpg://user:pw@host:5432/db`). Vacío = marketer arranca en modo degraded (sin DB, solo actions hardcoded fallback). |
| `DB_POOL_SIZE` | `10` | Pool de conexiones asyncpg. |
| `DB_POOL_MAX_OVERFLOW` | `5` | Conexiones extra en burst. |
| `DB_POOL_TIMEOUT_SECONDS` | `10` | Timeout al pedir conexión del pool. |
| `RUNS_RETENTION_DAYS` | `90` | Retención de `marketer_runs`. 0 = sin límite. |
| `CLIENT_MEMORY_TTL_DAYS` | `0` | 0 = memory no expira. >0 = purgar memory de clientes inactivos. |
| `ACTIONS_CACHE_TTL_SECONDS` | `60` | TTL del cache in-memory de `marketer_actions`. |

---

## 7. Deploy

### 7.1 Provisioning

**Decisión pendiente:** ¿DB dedicada o compartida con router?

| Opción | Pro | Contra |
|---|---|---|
| Dedicada (marketer tiene su propia DB) | Aislamiento, ownership clara, cada agente puede evolucionar su schema sin coordinar con router | Otra DB que mantener |
| Compartida con router | Una sola DB, misma instancia, consulta cruzada posible | Acoplamiento schema-level, risk de deadlocks cross-servicio |

**Recomendación**: **dedicada**. Es más limpio para un MVP y permite escalar marketer independiente.

### 7.2 Migración

```bash
# Desde el container o localmente
alembic upgrade head
```

El Dockerfile puede optar por correr `alembic upgrade head` en entrypoint (migraciones al arrancar) o delegarlo a un job de deploy separado (recomendado para producción — evitar que dos replicas intenten migrar concurrente).

### 7.3 Readiness

`GET /ready` debe verificar:
1. `GEMINI_API_KEY` present.
2. **DB pool está connectable** (ping al pool).
3. **`marketer_actions` contiene al menos una action con `is_enabled=true`** (sanity check de que las seeds corrieron).

Fallo en cualquiera → `{"status": "unhealthy", "detail": "..."}`.

### 7.4 Graceful degradation (DB down)

Si la DB cae durante operación:
- **Actions**: fallback al cache in-memory. Si el cache también está vacío, fallback a lista hardcoded (`create_post`, `edit_post` enabled; web disabled). Log CRITICAL.
- **Client memory**: continuar con `client_memory=None`. La run corre sin memory; el post se genera pero pierde el beneficio de consistency.
- **Persistence**: el INSERT en `marketer_runs` falla. Log CRITICAL + alerta. El callback a router se hace igual (CF no se ve afectado). La run queda sin audit trail.

Ninguna DB failure bloquea el path crítico (callback al router). Marketer degrada con warnings, no con errores.

---

## 8. Open items (decisiones de producto/infra)

1. **Dedicated vs shared DB** — ver §7.1. Pide opinión al infra team.
2. **Hash del brief** — ¿incluir qué campos? Recomiendo todo `brief.form_values` + `profile` relevantes (business_name, tone, palette). Excluir timestamps y metadata volátil.
3. **Política de retención de runs** — 90 días por default es balance razonable entre audit y cost. Legal puede pedir 1 año.
4. **Privacidad** — `marketer_runs.envelope` y `.enrichment` contienen brief completo = posible PII (teléfono, email del cliente). Políticas: encryption at rest (Postgres nativo), acceso restringido al schema, GDPR-style delete por `account_uuid` cuando cliente pide "olvidarme".
5. **Memory granularity** — hoy es 1 memory por `account_uuid`. ¿Qué pasa si un cliente tiene múltiples brands (multi-location)? Re-key por `(account_uuid, brief_uuid)` cuando caso exista.
6. **Actions ownership** — ¿quién edita `marketer_actions`? Panel admin de marketer o DBAs con SQL directo. MVP: SQL directo; admin UI post-MVP.

---

## 9. Roadmap de implementación

Prioridad en PRs separados:

1. **PR-1: schema + migrations + repositories (no flow wiring)**. Crear `db/` module, modelos SQLAlchemy, migration `001_initial.py`, repositories básicos con tests unit. Sin cambios en `reasoner.py` todavía.
2. **PR-2: actions loaded from DB**. `_parse_action_code` y `OVERLAYS` mapping ahora consultan `marketer_actions` cache. Fallback hardcoded si DB down.
3. **PR-3: run persistence**. Tras COMPLETED/FAILED, INSERT en `marketer_runs`. Sin memory update aún.
4. **PR-4: client_memory write path**. Tras COMPLETED, UPSERT en `marketer_client_memory` con los nuevos angles, pillars, cta distribution.
5. **PR-5: client_memory read path + prompt injection**. Al arrancar el request, SELECT memory; inyectar en prompt via nueva sección `# Client memory`.
6. **PR-6: tests de integración con testcontainers-postgres**. Cobertura end-to-end con DB real.

Cada PR es mergeable solo, con tests. Entre 1 y 2 días cada uno.

---

## 10. Resumen

- **Qué agregamos**: 3 tablas Postgres — `marketer_actions` (configurable actions), `marketer_runs` (audit append-only), `marketer_client_memory` (agregado por cliente).
- **Por qué**: actions sin deploy + memoria por cliente para consistency + audit/replay para debug + feed a subagentes futuros.
- **Qué retira**: NFR-8 "purity / no persistence" del SPEC v2 queda cancelado.
- **Qué NO**: esto no toca el contrato con ROUTER. Router sigue viéndonos igual. Marketer internamente ahora recuerda.
- **Siguiente paso**: confirmar DB dedicada vs compartida, y arrancar PR-1 (schema + migrations).
