# Marketer Agent — Dev Instructions for Claude

## Golden Reference

### Input fixture (the only real one)
`tests/fixtures/envelopes/nubiex_golden_input.json` — real envelope from Nubiex Men's Massage by Bruno.  
All other fixtures are synthetic and live in `tests/fixtures/envelopes/`. Unit tests load from there.

### Output quality target
`docs/GOLDEN REFERENCE.md` — defines the quality bar for PostEnrichment v2.  
Contains: CONCEPT block target, Brand DNA format, Carousel format, inline ✓/✗ examples.  
This is the only reference for judging prompt quality. Do not derive targets from elsewhere.

---

## Testing workflow

### Viewing results: always use Inspector
When asked to run a test, review output, or inspect results:

```bash
python scripts/ops/inspector.py          # last 5 runs from Postgres
python scripts/ops/inspector.py --limit 10
```

Output: `reports/inspector.html` — overwritten in place, just refresh the browser tab.  
**Do not build custom HTML dashboards. Do not open files in `docs/archive/legacy/`. Inspector is the only viewer.**

### Quick iteration (no DB required)
```bash
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/quick_run.py
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/quick_run.py "Descripción custom"
MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/quick_run.py --scenario 3
```
Runs one scenario against `tests/fixtures/envelopes/nubiex_post.json`, appends to `reports/quick_runs.json`, opens `reports/nubiex_dashboard.html`.  
10 pre-defined Nubiex scenarios (post, story, reel, carousel across different pillars).

### Full smoke (DB + Gemini)
```bash
MARKETER_RUN_LIVE=1 python scripts/ops/db_e2e_smoke.py
python scripts/ops/db_e2e_smoke.py --description "Crea una story sobre..."
```
Posts `tests/fixtures/envelopes/nubiex_golden_input.json` → real Gemini → Postgres → calls inspector at the end. Costs one LLM call.

### Async dispatch smoke
```bash
PYTHONPATH=src python scripts/ops/smoke_async_roundtrip.py
```
Spins up a mock callback server and validates the full async PATCH flow. No LLM.

### Unit tests (no LLM, no DB)
```bash
pytest
```
Covers: normalizer, validator, async dispatch. All fixtures load from `tests/fixtures/envelopes/`.

### POST any fixture manually
```bash
python scripts/dev/run_fixture.py nubiex_golden_input.json
python scripts/dev/run_fixture.py tests/fixtures/envelopes/nubiex_golden_input.json
```
POSTs to a running local server (`http://127.0.0.1:8000` by default).

---

## Active scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `scripts/dev/quick_run.py` | Fast iteration against nubiex fixture, 10 scenarios |
| `scripts/dev/run_fixture.py` | POST any fixture to a running server |
| `scripts/ops/db_e2e_smoke.py` | Full golden run: Gemini + Postgres |
| `scripts/ops/smoke_async_roundtrip.py` | Async dispatch smoke with mock callback server |
| `scripts/ops/inspector.py` | Render `reports/inspector.html` from DB — **primary test viewer** |

Demo/exploration scripts live in `scripts/demo/`. Everything in `docs/archive/legacy/` is archived.

---

## Repo layout (non-legacy)

```
tests/fixtures/envelopes/nubiex_golden_input.json  ← the only real input fixture
docs/GOLDEN REFERENCE.md                           ← quality targets for output
docs/ROUTER CONTRACT.md                            ← router ↔ marketer interface
docs/examples/runs/                                ← sample run outputs (reference)
images/                                            ← Nubiex brand images (4 valores JPGs)
reports/                                           ← inspector.html + run logs (runtime, gitignored)
scripts/dev/                                       ← iteration dev tools
scripts/ops/                                       ← smoke + inspector
scripts/demo/                                      ← demo/exploration scripts
src/marketer/                                      ← agent source
tests/                                             ← pytest suite + fixtures
docs/archive/legacy/                               ← archived; not wired to anything active
```

---

## Key facts

- The prod pipeline is the FastAPI app in `src/marketer/main.py`. Scripts are dev tools only.
- `tests/fixtures/envelopes/` — all fixtures (real + synthetic) used by the test suite.
- `reports/` artifacts are runtime output — do not commit them.
- DB schema source of truth: `alembic/versions/001_initial_schema.py`.
- `docs/PRD.md` is outdated. Trust the code, `docs/ROUTER CONTRACT.md`, and `docs/GOLDEN REFERENCE.md`.
