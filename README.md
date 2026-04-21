# Marketer Agent

> Strategic post-enrichment microservice for the Plinng pipeline.
> Receives tasks from **ROUTER**, produces enrichment v2 for **CONTENT_FACTORY**.

**Docs autoritativas:**
- [`PRD.md`](./PRD.md) — qué hace y por qué (scope MVP, outputs, non-goals)
- [`SPEC.md`](./SPEC.md) — cómo se integra (contratos, env vars, deploy, testing)
- [`docs/ROUTER CONTRACT.md`](./docs/ROUTER%20CONTRACT.md) — contrato del orquestador (fuente externa)

---

## TL;DR

```
ROUTER ─POST /tasks─► marketer ─202 ACK─► ROUTER
                        │
                        ├── reason() en background (~12s, Gemini)
                        │
                        └── PATCH callback_url ─► ROUTER ─► CONTENT_FACTORY
```

**Stack:** Python 3.11+, FastAPI, Pydantic v2, `google-genai` SDK. Async I/O. Sin persistencia.

**Scope MVP (2026-04-21):** solo `create_post` / `edit_post`. Web bloqueado por candado en `reasoner.py`.

---

## Setup local

### 1. Entorno

```bash
# Crear venv
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
.venv\Scripts\activate       # Windows

# Instalar deps
pip install -r requirements.txt
```

### 2. Env vars

```bash
cp .env.example .env
# Editar .env y poner al menos GEMINI_API_KEY
```

Variables (ver `SPEC.md §10` para la tabla completa):

- `GEMINI_API_KEY` — **requerida**
- `GEMINI_MODEL` — default `gemini-3-flash-preview`
- `INBOUND_TOKEN` — bearer esperado en `Authorization`. Vacío en dev.
- `ORCH_CALLBACK_API_KEY` — `X-API-Key` que marketer manda en PATCH al router. Vacío en dev.

### 3. Correr

```bash
# Dev (con reload)
PYTHONPATH=src python -m uvicorn marketer.main:app --reload --port 8000

# Sin reload
PYTHONPATH=src python -m uvicorn marketer.main:app --host 0.0.0.0 --port 8000
```

Verificar:

```bash
curl http://localhost:8000/health   # {"status":"healthy"}
curl http://localhost:8000/ready    # {"status":"ready"} si GEMINI_API_KEY set
```

### 4. Probar un fixture

```bash
PYTHONPATH=src python scripts/run_fixture.py casa_maruja_post.json
```

O smoke E2E completo (uvicorn + mock callback server):

```bash
PYTHONPATH=src python scripts/smoke_async_roundtrip.py
```

---

## Testing

### Offline (rápido, <2s, sin Gemini)

```bash
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_golden_casa_maruja.py -v
```

Cubre: normalizer (14), validator (10), main async (12) = 36 tests.

### Live (~20s, 1 llamada Gemini)

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python -m pytest tests/test_golden_casa_maruja.py -v
```

Corre el pipeline real contra Casa Maruja fixture. 26 tests: schema shape, cta coherence, visual selection, brand_dna sections, brand_intelligence fields.

### Batch cross-vertical (~100s, 9 llamadas Gemini)

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/batch_test.py
```

3 fixtures × 3 runs → `reports/batch_test_<date>.md` con consistency de decisiones entre runs.

### Demo visual multi-vertical

```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/build_multi_demo_html.py
```

Corre 6 fixtures en paralelo, genera `samples/marketer_demo_v2.html` con tabla comparativa + tabs navegables.

---

## Estructura del repo

```
src/marketer/           # Código del servicio
  main.py               # FastAPI app (/tasks 202 + /tasks/sync dev + /health + /ready)
  reasoner.py           # Pipeline: normalize → LLM → validate
  normalizer.py         # Envelope → InternalContext (pure)
  validator.py          # Chequeos determinísticos post-LLM
  llm/                  # Gemini wrapper + prompts
  schemas/              # Pydantic models (envelope, internal_context, enrichment)

tests/                  # 62 tests (36 offline + 26 live)
fixtures/envelopes/     # 9 fixtures de prueba (casa_maruja, saas, retail, dental, etc.)
golden/posts/           # 3 baselines v2 para regression detection
scripts/                # run_fixture, smoke_async, batch_test, build_demo_html
samples/                # HTMLs demo generados (marketer_demo_v2.html)
docs/                   # Docs del router (externas al repo, referencia)

PRD.md                  # Producto
SPEC.md                 # Técnico + operacional + integration runbook
```

---

## Integración

**Para conectar a ROUTER real**, ver `SPEC.md §14 Integration runbook`. Resumen:

1. Infra team despliega container (ver `SPEC.md §13`).
2. Router team registra marketer en `agents` + `action_catalog` + `agent_sequence` (SQL en `SPEC §14.2`).
3. Smoke E2E: `POST /api/v1/jobs` al router → verificar PATCH llega a router desde marketer en ~15s.

Variables a acordar con router team: `INBOUND_TOKEN` (mismo valor en `agents.auth_token` del router y en env de marketer) y `ORCH_CALLBACK_API_KEY` (per-agent API key que router asigna).

---

## Estado

- ✅ MVP funcional, 62/62 tests verdes
- ✅ Shape v2 con `brand_dna` (público, para CF) + `brand_intelligence` (interno, para subagentes) + `cf_post_brief`
- ✅ Contrato async listo para ROUTER (ACK 202 + PATCH callback)
- ⏳ Deploy pendiente (infra team)
- ⏳ Registro en router pendiente (router team)
