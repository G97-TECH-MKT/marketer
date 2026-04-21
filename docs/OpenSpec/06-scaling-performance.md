# 06 — Scaling & Performance

**Version:** 2.0  
**Last Updated:** 2026-04-21

---

## 1. Concurrency Model

### 1.1 Request Flow

```
HTTP Request
    │
    ▼
uvicorn (async ASGI, single worker)
    │
    ▼
FastAPI route handler (async)
    │
    ├─ Validate (sync, <5ms)
    ├─ Enqueue BackgroundTask
    └─ Return 202 immediately
         │
         ▼ (async, concurrent with next requests)
    BackgroundTask coroutine
         │
         └─ asyncio.to_thread(reason, envelope)
                  │
                  ▼ (thread pool, blocking)
              normalize → prompt → Gemini call → validate → callback
```

### 1.2 Event Loop vs Thread Pool

| Operation | How it runs | Blocks event loop? |
|-----------|-------------|-------------------|
| Request parsing | Async (event loop) | No |
| `reason()` orchestration | Thread (via `to_thread`) | No |
| `normalizer.normalize()` | Sync in thread | No |
| Gemini HTTP call | Sync in thread (google-genai SDK) | No |
| Callback `httpx.patch()` | Async (if called in async context) | No |

The key constraint: `asyncio.to_thread()` submits to a thread pool. The default pool size is `min(32, cpu_count + 4)`. On a 0.25 vCPU Fargate task, `cpu_count = 2` → pool size = 6. This means **~6 concurrent background tasks per replica** by default.

**To increase concurrency per replica:**

```python
# In main.py startup
import asyncio
loop = asyncio.get_event_loop()
executor = concurrent.futures.ThreadPoolExecutor(max_workers=20)
loop.set_default_executor(executor)
```

Or switch to an async Gemini client (when available) to avoid the thread pool entirely.

### 1.3 Per-Replica Throughput

At default pool size (6 threads):
- Each task takes ~12s
- 6 concurrent tasks × (1/12 tasks/s each) = **~0.5 tasks/second per replica**

At max pool size (20 threads):
- 20 × (1/12) = **~1.7 tasks/second per replica**

With horizontal scaling to 10 replicas:
- **~17 tasks/second** (limited by Gemini quota, not compute)

---

## 2. Latency Budget

### 2.1 Component Breakdown (per task)

| Phase | p50 | p95 | p99 | Bottleneck |
|-------|-----|-----|-----|------------|
| Normalize | 5ms | 15ms | 30ms | CPU (Python) |
| Prompt build | 2ms | 5ms | 10ms | CPU (string ops) |
| Gemini call | 8s | 14s | 22s | **Network + LLM** |
| Repair (if needed) | 3s | 6s | 10s | Network + LLM |
| Validate | 2ms | 5ms | 15ms | CPU |
| Callback (PATCH) | 150ms | 400ms | 800ms | Network |
| **Total (no repair)** | **~8.2s** | **~15s** | **~23s** |  |
| **Total (with repair)** | **~11s** | **~20s** | **~30s** |  |

### 2.2 ACK Latency (POST → 202)

| Phase | p50 | p95 |
|-------|-----|-----|
| JSON parsing | 1ms | 3ms |
| Pydantic validation | 2ms | 5ms |
| Header auth check | <1ms | <1ms |
| BackgroundTask enqueue | <1ms | <1ms |
| **Total** | **~5ms** | **~15ms** |

The actual observed p95 of ~300–500ms in production is dominated by ALB + TLS handshake + Fargate network overhead, not application logic.

---

## 3. Gemini Quota Management

### 3.1 Default Quotas

| Quota | Free Tier | Paid (Flash) |
|-------|-----------|-------------|
| Requests per minute (RPM) | 15 | 1,000+ (negotiated) |
| Tokens per minute (TPM) | 1,000,000 | 4,000,000+ |
| Tokens per day (TPD) | 1,500,000 | Unlimited (billed) |

### 3.2 Token Usage per Request

| Component | Tokens |
|-----------|--------|
| System prompt | ~800 |
| Action overlay | ~200 |
| InternalContext (serialized) | ~1,500–3,000 |
| **Input total** | ~2,500–4,000 |
| PostEnrichment output | ~800–1,200 |
| **Total per request** | ~3,300–5,200 |

### 3.3 Quota Mitigation Strategies

**For MVP (< 200 requests/day):** Free tier is sufficient.

**For production (> 200 requests/day):**
1. Upgrade to Gemini paid tier
2. Request quota increase via Google Cloud Console
3. Implement prompt caching for brand_dna and system prompt (saves ~30% tokens)

**Rate limit handling:**

The google-genai SDK raises `google.api_core.exceptions.ResourceExhausted` (HTTP 429) on quota exhaustion. This surfaces as `internal_error: ResourceExhausted: ...` in the FAILED callback.

**Recommended:** Add retry logic in `llm/gemini.py` for 429 with exponential backoff (1s, 4s, 16s). Cap at 3 retries.

```python
# Add to gemini.py
import tenacity

@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=16),
    retry=tenacity.retry_if_exception_type(google.api_core.exceptions.ResourceExhausted)
)
async def generate_structured_with_retry(self, ...):
    ...
```

---

## 4. Scaling Strategy

### 4.1 Horizontal Scaling (ECS)

```
Replicas needed = ceil(peak_requests_per_second / throughput_per_replica)

Example: 10 tasks/second peak
→ 10 / 0.5 = 20 replicas needed (default pool)
→ 10 / 1.7 = 6 replicas needed (max pool size)
```

**Auto-scaling policy:**
- **Metric:** `ECSServiceAverageCPUUtilization`
- **Target:** 70%
- **Scale-out cooldown:** 60s (fast response to load)
- **Scale-in cooldown:** 300s (avoid thrashing)

**Limitation:** CPU utilization is a poor proxy for this workload (mostly I/O-bound). For better accuracy, use ALB `RequestCountPerTarget` metric:

```hcl
target_tracking_scaling_policy_configuration {
  target_value = 5.0  # 5 requests/target in flight
  customized_metric_specification {
    metric_name = "RequestCountPerTarget"
    namespace   = "AWS/ApplicationELB"
    statistic   = "Average"
    dimensions = [{
      name  = "TargetGroup"
      value = var.target_group_arn_suffix
    }]
  }
}
```

### 4.2 Vertical Scaling

| Scenario | CPU | Memory | Notes |
|----------|-----|--------|-------|
| Current (MVP) | 0.25 vCPU | 512 MB | Sufficient |
| 20+ concurrent tasks | 0.5 vCPU | 1 GB | Thread pool expansion |
| Multimodal (future) | 1 vCPU | 2 GB | Image processing overhead |
| DB-heavy (future) | 0.5 vCPU | 1 GB | Connection pool overhead |

### 4.3 Regional Scaling (Multi-Region)

For latency-sensitive deployments or regional compliance:

```
us-east-1 (primary)
    └─ ECS cluster + RDS (primary)

eu-west-1 (secondary, optional)
    └─ ECS cluster + RDS (replica)
    
Route53 Latency Routing:
    marketer.internal.plinng.io → closest region
```

Multi-region adds complexity; only warranted if:
- Gemini latency from non-US region is significantly different
- Data residency requirements

---

## 5. Performance Optimization Roadmap

### Phase 1 (Current, MVP)
- Single Gemini call, synchronous in thread pool
- No caching, no persistence
- Horizontal scaling via ECS replicas

### Phase 2 (Near-term)
**Async Gemini client:**
- When google-genai supports async natively, remove `asyncio.to_thread()`
- Eliminates thread pool bottleneck
- Enables 50+ concurrent tasks per replica

**Response caching (brand_dna):**
```python
# Cache serialized brand_dna per account_uuid (TTL: 24h)
# Saves ~$0.0005/request and ~500ms
from functools import lru_cache

@lru_cache(maxsize=1000)
def get_cached_brand_dna(account_uuid: str, brief_hash: str) -> str | None:
    ...
```

### Phase 3 (Future)
**Gemini prompt caching:**
- Cache system prompt + action overlay (~1,000 tokens)
- Saves ~20–30% on token costs at scale
- Requires Gemini caching API support

**Request queuing (SQS/Redis):**
- Instead of BackgroundTask (in-process), push to SQS
- Separate worker fleet processes queue
- Enables retry, DLQ, visibility timeout
- Higher durability but adds latency and cost

```
POST /tasks → SQS enqueue → 202
                │
                ▼ (separate consumer)
            Worker picks up → pipeline → PATCH callback
```

**Batch processing:**
- Multiple tasks per Gemini call (if API supports)
- Currently not feasible with structured output requirement

---

## 6. Load Testing

### 6.1 Tools

```bash
# k6 script for sustained load test
k6 run --vus 20 --duration 5m scripts/k6_load_test.js

# wrk for raw ACK throughput
wrk -t 10 -c 100 -d 30s \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --script scripts/wrk_post_tasks.lua \
  https://marketer.internal.plinng.io/tasks
```

### 6.2 Baseline Metrics (1 Fargate task, 0.25 vCPU)

| Metric | Value |
|--------|-------|
| Max sustained ACK rate | ~500 req/min |
| Max concurrent background tasks | 6 (thread pool) |
| Max sustained throughput | ~0.5 tasks/sec |
| Memory at max load | ~150 MB |
| CPU at max load | ~25% (mostly idle waiting on Gemini) |

### 6.3 Bottleneck Identification

```
High latency → Gemini API (network, quota)
High FAILED rate → Check GEMINI_API_KEY, quota, model availability
High memory → Normalizer with large gallery (check gallery cap at 20)
High CPU → Not expected; investigate prompt serialization
```

---

## 7. Capacity Planning

### 7.1 Request Volume Projections

| Clients | Posts/client/day | Requests/day | Peak RPM | Replicas needed |
|---------|-----------------|-------------|---------|----------------|
| 10 | 5 | 50 | 1 | 1 |
| 100 | 5 | 500 | 10 | 2 |
| 1,000 | 5 | 5,000 | 100 | 4 |
| 10,000 | 5 | 50,000 | 1,000 | 20+ |

### 7.2 Cost at Scale

| Replicas | Gemini cost/month | Fargate cost/month | Total |
|----------|------------------|-------------------|-------|
| 2 | ~$30 | ~$30 | ~$160 |
| 5 | ~$300 | ~$75 | ~$475 |
| 20 | ~$3,000 | ~$300 | ~$3,400 |

> At scale, Gemini API cost dominates. Prompt caching can reduce this by 20–30%.
