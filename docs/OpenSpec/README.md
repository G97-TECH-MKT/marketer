# Marketer — OpenSpec Documentation Index

**Version:** 2.0  
**Last Updated:** 2026-04-22  
**Status:** Production-Ready MVP  
**Maintainer:** Orbidi Platform Team

---

## What Is This?

Marketer is the AI-driven marketing enrichment microservice in the Plinng pipeline. It receives task envelopes from the ROUTER orchestrator, applies strategic marketing reasoning via Google Gemini, and delivers structured `PostEnrichment` objects to `CONTENT_FACTORY` for final content generation.

This OpenSpec directory contains the authoritative technical reference for:

- Architecture and system design decisions
- Complete API reference (request/response contracts)
- All data schemas and their validation rules
- AWS deployment architecture (economic, secure, Terraform-ready)
- Scaling strategy and performance characteristics
- Security model and threat mitigations
- Integration guide for upstream and downstream consumers
- Operations runbook (monitoring, alerting, incident response)

---

## Document Index

| # | Document | Description |
|---|----------|-------------|
| [01](./01-overview.md) | **System Overview** | Architecture, data flow, tech stack, design decisions |
| [02](./02-api-reference.md) | **API Reference** | All endpoints, request/response shapes, error codes |
| [03](./03-data-models.md) | **Data Models** | Complete schema reference with validation rules |
| [04](./04-aws-deployment.md) | **AWS Deployment** | Architecture, cost model, deployment guide |
| [05](./05-terraform.md) | **Terraform Reference** | Complete IaC modules, environments, CI/CD |
| [06](./06-scaling-performance.md) | **Scaling & Performance** | Concurrency model, scaling strategy, benchmarks |
| [07](./07-security.md) | **Security Architecture** | Threat model, controls, secrets management |
| [08](./08-integration-guide.md) | **Integration Guide** | ROUTER contract, CONTENT_FACTORY contract, testing |
| [09](./09-operations.md) | **Operations Runbook** | Monitoring, alerting, incident response, maintenance |
| [10](./10-user-profile-integration.md) | **User Profile Integration** | USP Memory Gateway GraphQL client, data mapping, precedence rules |
| [11](./11-gallery-image-pool.md) | **Gallery Image Pool** | Brand media fetch, locking semantics, LLM vision-guided selection, downstream image delivery |

---

## Quick Reference

### Service Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/tasks` | POST | Main ingress — async task dispatch (202 ACK) |
| `/tasks/sync` | POST | Dev-only — synchronous inline response |
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe (gates on GEMINI_API_KEY) |

### Critical Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | **YES** | Google Gemini API key |
| `INBOUND_TOKEN` | Prod | Bearer token for inbound auth |
| `ORCH_CALLBACK_API_KEY` | Prod | API key for PATCH callback |
| `DATABASE_URL` | Optional | PostgreSQL URL (empty = degraded mode) |

### Technology Stack

```
Runtime:    Python 3.11+ / FastAPI / asyncio
LLM:        Google Gemini (google-genai SDK)
Schema:     Pydantic v2
HTTP:       httpx (async, with retry)
DB:         PostgreSQL / asyncpg (planned)
Container:  Docker (python:3.11-slim)
```

### Current Supported Actions

| Action Code | Status | Notes |
|-------------|--------|-------|
| `create_post` | ✅ Enabled | Instagram post enrichment |
| `edit_post` | ✅ Enabled | Requires `prior_post` context |
| `create_web` | 🔒 Blocked | Overlay ready; pending ATLAS |
| `edit_web` | 🔒 Blocked | Overlay ready; pending ATLAS |

---

## Architecture Diagram (High Level)

```
                    ┌─────────────────────────────────────────┐
                    │              AWS VPC (private)           │
                    │                                          │
  ROUTER ──POST /tasks──► ALB ──► ECS Fargate Task             │
    │                    │        [marketer container]          │
    │                    │         │                            │
    │                    │         ├── Gemini API (egress)      │
    │                    │         ├── Secrets Manager          │
    │                    │         └── RDS PostgreSQL (future)  │
    │                    │                                      │
    └──◄ PATCH callback──┘        CloudWatch Logs + Metrics     │
                    │                                          │
                    └─────────────────────────────────────────┘
```

---

## Pipeline at a Glance

```
POST /tasks (202 in ~300ms)
    │
    └── Background worker:
         1. Normalize   envelope → InternalContext          (~5ms)
         2. Prompt      context → Gemini prompt             (~2ms)
         3. LLM Call    Gemini structured output            (~10-14s)
         4. Repair      if schema failure, 1 retry          (~0-5s)
         5. Validate    deterministic checks + corrections  (~2ms)
         6. Callback    PATCH callback_url                  (~200ms)
```

**End-to-end p50:** ~12s | **p95:** ~18s | **p99:** <30s

---

## Repository Structure

```
marketer/
├── src/marketer/          # Application source
│   ├── main.py            # FastAPI app + route handlers
│   ├── config.py          # Pydantic settings
│   ├── reasoner.py        # Pipeline orchestrator
│   ├── normalizer.py      # Envelope → InternalContext
│   ├── validator.py       # Post-LLM deterministic checks
│   ├── schemas/           # Pydantic models
│   └── llm/               # Gemini wrapper + prompts
├── tests/                 # 62 tests (36 offline + 26 live)
├── tests/fixtures/envelopes/  # 9 test envelopes
├── tests/golden/posts/        # 3 regression baselines
├── scripts/               # Dev/smoke test utilities
├── docs/OpenSpec/         # ← This directory
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── .env.example
```
