# 09 — Operations Runbook

**Version:** 2.0  
**Last Updated:** 2026-04-21  
**On-Call:** Orbidi Platform Team

---

## 1. Service Health

### 1.1 Health Endpoints

```bash
# Liveness (is the process running?)
curl https://marketer.internal.plinng.io/health
# Expected: {"status": "healthy"}

# Readiness (is it ready to serve?)
curl https://marketer.internal.plinng.io/ready
# Expected: {"status": "ready"}
# Unhealthy: {"status": "unhealthy", "detail": "GEMINI_API_KEY not set"}
```

### 1.2 Service Status Check (AWS CLI)

```bash
# Check ECS service state
aws ecs describe-services \
  --cluster marketer-prod \
  --services marketer \
  --query 'services[0].{desired:desiredCount, running:runningCount, pending:pendingCount, status:status}'

# Check task health
aws ecs list-tasks \
  --cluster marketer-prod \
  --service-name marketer \
  --desired-status RUNNING

# Check recent deployments
aws ecs describe-services \
  --cluster marketer-prod \
  --services marketer \
  --query 'services[0].deployments[*].{id:id,status:status,desired:desiredCount,running:runningCount,created:createdAt}'
```

### 1.3 ALB Health

```bash
# Check target group health
aws elbv2 describe-target-health \
  --target-group-arn $(terraform output -raw target_group_arn) \
  --query 'TargetHealthDescriptions[*].{id:Target.Id, port:Target.Port, state:TargetHealth.State}'
```

---

## 2. Deployment Procedures

### 2.1 Standard Deployment (CI/CD)

Triggered automatically on push to `main`:
1. Tests pass (62 offline tests, mypy)
2. Docker build + ECR push
3. Terraform apply with new `image_tag`
4. ECS rolling update (circuit breaker enabled)
5. CloudWatch alarm check (5 min post-deploy)

### 2.2 Manual Deployment

```bash
# Build and push
cd /path/to/marketer
IMAGE_TAG=$(git rev-parse --short HEAD)
ECR_URI=$(aws ecr describe-repositories \
  --repository-names marketer \
  --query 'repositories[0].repositoryUri' \
  --output text)

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI

docker build -t marketer:$IMAGE_TAG .
docker tag marketer:$IMAGE_TAG $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:$IMAGE_TAG

# Deploy via Terraform
cd terraform/environments/prod
terraform apply -var="image_tag=$IMAGE_TAG" -auto-approve

# Watch deployment progress
aws ecs wait services-stable \
  --cluster marketer-prod \
  --services marketer
echo "Deployment complete"
```

### 2.3 Emergency Rollback

```bash
# Find previous task definition revision
CURRENT=$(aws ecs describe-services \
  --cluster marketer-prod \
  --services marketer \
  --query 'services[0].taskDefinition' \
  --output text)

echo "Current: $CURRENT"
PREV_REV=$(( $(echo $CURRENT | cut -d: -f7) - 1 ))
PREV_TD="$(echo $CURRENT | sed 's/:[0-9]*$/'):$PREV_REV"
echo "Rolling back to: $PREV_TD"

# Execute rollback
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --task-definition $PREV_TD \
  --force-new-deployment

# Wait for stable
aws ecs wait services-stable --cluster marketer-prod --services marketer
```

### 2.4 Zero-Downtime Deployment Verification

After any deployment:

```bash
# 1. Check health endpoint
curl -f https://marketer.internal.plinng.io/ready

# 2. Check ECS running count matches desired
aws ecs describe-services \
  --cluster marketer-prod \
  --services marketer \
  --query 'services[0].{desired:desiredCount, running:runningCount}'

# 3. Send a test task via /tasks/sync
curl -X POST https://marketer.internal.plinng.io/tasks/sync \
  -H "Authorization: Bearer $INBOUND_TOKEN" \
  -H "Content-Type: application/json" \
  -d @fixtures/envelopes/minimal_post.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d['status']=='COMPLETED' else 'FAIL')"
```

---

## 3. Monitoring & Alerting

### 3.1 Key Metrics

| Metric | Normal | Warning | Critical |
|--------|--------|---------|----------|
| ECS running tasks | ≥ min_capacity | < min_capacity | 0 |
| ALB 5xx rate | < 1% | 1–5% | > 5% |
| ALB 4xx rate | < 5% | 5–15% | > 15% |
| p95 latency | < 20s | 20–25s | > 25s |
| Gemini timeout rate | < 1% | 1–3% | > 3% |
| schema_repair_used rate | < 5% | 5–10% | > 10% |
| FAILED callback rate | < 2% | 2–5% | > 5% |
| ECS CPU utilization | < 50% | 50–70% | > 70% (triggers scale-out) |

### 3.2 CloudWatch Logs Queries

```
Log Group: /ecs/marketer
```

**Failed tasks in last hour:**
```
fields @timestamp, task_id, error_message
| filter status = "FAILED"
| sort @timestamp desc
| limit 50
```

**Latency percentiles (last 24h):**
```
fields latency_ms
| filter ispresent(latency_ms)
| stats pct(latency_ms, 50) as p50,
        pct(latency_ms, 95) as p95,
        pct(latency_ms, 99) as p99,
        avg(latency_ms) as avg_ms
  by bin(1h)
```

**Warning distribution (last 7 days):**
```
fields @message
| parse @message '"code": "*"' as warning_code
| filter ispresent(warning_code)
| stats count(*) as n by warning_code
| sort n desc
```

**Gemini timeout rate:**
```
fields @message
| filter @message like "TimeoutError"
| stats count(*) as timeouts by bin(1h)
```

**Callback failures:**
```
fields @message, task_id
| filter @message like "callback_failed"
| sort @timestamp desc
```

**Repair rate (model quality indicator):**
```
fields @message
| filter @message like "schema_repair_used"
| stats count(*) as repairs by bin(1h)
```

### 3.3 Dashboards

Access the CloudWatch dashboard: `marketer-{env}`

- Bookmark URL: `https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=marketer-prod`
- Auto-refreshes every 60 seconds
- Covers: request rate, error rate, latency, task count, CPU/memory

---

## 4. Incident Response

### 4.1 P1: Service Completely Down (0 running tasks)

**Symptoms:** All requests return 502/503; `/health` unreachable.

**Diagnosis:**
```bash
# Check ECS
aws ecs describe-services \
  --cluster marketer-prod --services marketer \
  --query 'services[0].{desired:desiredCount, running:runningCount, events:events[:3]}'

# Check task failures
aws ecs list-tasks \
  --cluster marketer-prod \
  --desired-status STOPPED \
  --query 'taskArns[0]'

# Get stopped task reason
aws ecs describe-tasks \
  --cluster marketer-prod \
  --tasks <task-arn> \
  --query 'tasks[0].containers[*].reason'
```

**Common causes & fixes:**

| Cause | Fix |
|-------|-----|
| GEMINI_API_KEY missing/invalid | Update Secrets Manager, force restart |
| ECR image pull failure | Check ECR lifecycle policy didn't delete image; push new image |
| VPC endpoint failure | Check sg-vpc-endpoints; check endpoint status |
| Task OOM (memory exceeded) | Increase task memory to 1 GB |
| Startup crash | Check CloudWatch logs for exception at startup |

**Resolution steps:**
1. Check CloudWatch logs: `/ecs/marketer` — look for startup errors
2. Fix root cause
3. Force deployment: `aws ecs update-service --force-new-deployment`
4. Watch: `aws ecs wait services-stable --cluster marketer-prod --services marketer`

### 4.2 P1: High Failure Rate (> 10% FAILED callbacks)

**Symptoms:** ROUTER receiving FAILED callbacks at high rate.

**Diagnosis:**
```bash
# Check error distribution
aws logs start-query \
  --log-group-name "/ecs/marketer" \
  --start-time $(date -d '30 minutes ago' +%s) \
  --end-time $(date +%s) \
  --query-string 'fields error_message | filter status = "FAILED" | stats count(*) as n by error_message | sort n desc'
```

**Common causes:**

| Error Pattern | Cause | Fix |
|---------------|-------|-----|
| `TimeoutError` | Gemini quota or latency spike | Check Gemini quota; increase LLM_TIMEOUT_SECONDS |
| `ResourceExhausted` | Gemini quota exceeded | Increase quota or add retry with backoff |
| `schema_validation_failed` | Gemini model regression | Check GEMINI_MODEL; rollback to previous model |
| `prior_post_missing` | ROUTER not sending prior_post | Fix ROUTER dispatch logic |
| `unsupported_action_code` | Unknown action_code | ROUTER registering wrong actions |

### 4.3 P2: High Latency (p95 > 25s)

**Symptoms:** Callbacks arriving late; ROUTER timeouts increasing.

**Diagnosis:**
```bash
# Check per-phase breakdown in logs
aws logs filter-log-events \
  --log-group-name "/ecs/marketer" \
  --filter-pattern '"latency_ms"' \
  --query 'events[*].message'
```

**Common causes:**

| Cause | Fix |
|-------|-----|
| Gemini API slow | Check Gemini status; monitor `gemini.google.com/status` |
| Callback URL slow (ROUTER) | Check ROUTER health; increase CALLBACK_HTTP_TIMEOUT_SECONDS |
| Undersized task (CPU throttling) | Increase task CPU to 512 |
| Thread pool saturation | Increase max_workers; add replicas |

### 4.4 P2: Schema Repair Rate Spike

**Symptoms:** `schema_repair_used` warnings appear > 10% of requests.

**Meaning:** Gemini is frequently returning JSON that doesn't parse against PostEnrichment schema on first attempt.

**Causes:**
- Gemini model was updated (google provides no notice of preview model changes)
- `GEMINI_MODEL` points to a deprecated/replaced model
- Prompt schema mismatch (after schema update without prompt update)

**Fix:**
1. Check if `GEMINI_MODEL` points to a valid model
2. Run live tests: `MARKETER_RUN_LIVE=1 pytest tests/test_golden_casa_maruja.py`
3. If test pass rate drops: rollback model or update prompts
4. Long-term: pin to a stable model version

### 4.5 P3: Callback Delivery Failures

**Symptoms:** Logs contain `callback_failed_after_N_attempts`.

**Meaning:** Marketer processed the task but could not deliver the result to ROUTER.

**Impact:** Task result is lost; ROUTER's timeout/retry mechanism must re-trigger.

**Diagnosis:**
```bash
# Find callback failures
aws logs filter-log-events \
  --log-group-name "/ecs/marketer" \
  --filter-pattern '"callback_failed"'
```

**Common causes:**

| Cause | Fix |
|-------|-----|
| ROUTER down during callback | ROUTER retry will re-trigger task |
| Invalid `callback_url` | Fix ROUTER dispatch; verify URL format |
| Wrong `ORCH_CALLBACK_API_KEY` | Rotate and sync keys |
| ROUTER 4xx on callback | Check ROUTER callback endpoint logs |

---

## 5. Maintenance Procedures

### 5.1 Secrets Rotation

**INBOUND_TOKEN (rotate every 90 days):**

```bash
# Generate new token
NEW_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

# Update Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id marketer/inbound-token \
  --secret-string "$NEW_TOKEN"

# Update ROUTER database (coordinate with ROUTER team)
# psql -h router-db -U admin -c "UPDATE agents SET auth_token='$NEW_TOKEN' WHERE name='marketer';"

# Restart Marketer to pick up new secret
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --force-new-deployment
```

**GEMINI_API_KEY rotation:**

```bash
# Get new key from Google Cloud Console
# Navigate to: console.cloud.google.com → APIs & Services → Credentials

aws secretsmanager put-secret-value \
  --secret-id marketer/gemini-api-key \
  --secret-string "AIzaSy..."

aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --force-new-deployment
```

### 5.2 Log Cleanup

CloudWatch log retention is configured at 30 days (dev) / 90 days (prod) by Terraform. No manual cleanup needed.

For cost optimization: enable log compression and S3 archival for logs older than 7 days.

### 5.3 ECR Image Cleanup

The lifecycle policy automatically retains only the last 10 tagged images and removes untagged images after 1 day. Manual cleanup:

```bash
# List all images
aws ecr list-images --repository-name marketer

# Delete specific image
aws ecr batch-delete-image \
  --repository-name marketer \
  --image-ids imageTag=sha-abc123
```

### 5.4 Gemini Model Updates

When updating `GEMINI_MODEL`:

1. Check model availability: run `fixtures` with new model on staging
2. Run golden tests: `GEMINI_MODEL=new-model MARKETER_RUN_LIVE=1 pytest tests/test_golden_casa_maruja.py`
3. Compare enrichment quality (check for regressions in schema repair rate)
4. Deploy via Terraform: update `gemini_model` tfvar
5. Monitor repair rate for 24h post-deploy

---

## 6. Capacity Procedures

### 6.1 Manual Scale-Out

```bash
# Scale to 5 tasks immediately
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --desired-count 5

# Scale back to auto-managed
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --desired-count 2  # return to min_capacity
```

### 6.2 Increase Max Capacity

```hcl
# In terraform/environments/prod/terraform.tfvars
max_capacity = 20  # increase from 10
```

```bash
cd terraform/environments/prod
terraform apply -var="max_capacity=20" -auto-approve
```

### 6.3 Gemini Quota Increase

If hitting `ResourceExhausted` errors:
1. Go to [Google Cloud Console](https://console.cloud.google.com/iam-admin/quotas)
2. Filter: `Gemini API` → `Requests per minute`
3. Click "Edit Quotas" → submit increase request
4. Quota increases typically approved within 24–48h

---

## 7. Environment Configuration Reference

### 7.1 All Environment Variables

| Variable | Default | Type | Notes |
|----------|---------|------|-------|
| `GEMINI_API_KEY` | — | str | **Required.** From Secrets Manager |
| `GEMINI_MODEL` | `gemini-2.5-flash-preview` | str | Configurable without redeploy |
| `LLM_TIMEOUT_SECONDS` | `30` | int | Increase if timeouts spike |
| `LOG_LEVEL` | `INFO` | str | DEBUG for troubleshooting |
| `EXTRAS_LIST_TRUNCATION` | `10` | int | Gallery extras cap |
| `INBOUND_TOKEN` | `""` | str | From Secrets Manager; empty=no auth |
| `ORCH_CALLBACK_API_KEY` | `""` | str | From Secrets Manager |
| `CALLBACK_HTTP_TIMEOUT_SECONDS` | `30.0` | float | Per callback attempt |
| `CALLBACK_RETRY_ATTEMPTS` | `2` | int | Total attempts including first |
| `DATABASE_URL` | `""` | str | Empty = no persistence (MVP) |
| `DB_POOL_SIZE` | `10` | int | PostgreSQL pool |
| `DB_POOL_MAX_OVERFLOW` | `5` | int | Burst connections |
| `DB_POOL_TIMEOUT_SECONDS` | `10` | int | Pool checkout timeout |
| `RUNS_RETENTION_DAYS` | `90` | int | DB audit log retention |
| `CLIENT_MEMORY_TTL_DAYS` | `0` | int | 0 = never expire |
| `ACTIONS_CACHE_TTL_SECONDS` | `60` | int | In-memory actions cache |

### 7.2 Recommended Production Values

```bash
GEMINI_MODEL=gemini-2.5-flash-preview
LLM_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
CALLBACK_HTTP_TIMEOUT_SECONDS=30.0
CALLBACK_RETRY_ATTEMPTS=2
EXTRAS_LIST_TRUNCATION=10
```

---

## 8. Runbook: First Deployment

Complete sequence for first production deployment:

```bash
# Step 1: Bootstrap Terraform state
cd terraform
aws s3api create-bucket --bucket orbidi-terraform-state --region us-east-1
aws s3api put-bucket-versioning --bucket orbidi-terraform-state \
  --versioning-configuration Status=Enabled
aws dynamodb create-table \
  --table-name terraform-state-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1

# Step 2: Generate secrets
GEMINI_API_KEY="AIzaSy..."  # from Google Cloud Console
INBOUND_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
CALLBACK_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

echo "INBOUND_TOKEN: $INBOUND_TOKEN"  # give to ROUTER team
echo "CALLBACK_API_KEY: $CALLBACK_API_KEY"  # give to ROUTER team to accept callbacks

# Step 3: Deploy infrastructure
cd environments/prod
terraform init
terraform plan \
  -var="gemini_api_key=$GEMINI_API_KEY" \
  -var="inbound_token=$INBOUND_TOKEN" \
  -var="orch_callback_api_key=$CALLBACK_API_KEY" \
  -var="image_tag=placeholder"
terraform apply [same -var flags] -auto-approve

# Step 4: Build and push initial image
cd /path/to/marketer
IMAGE_TAG=$(git rev-parse --short HEAD)
ECR_URI=$(aws ecr describe-repositories \
  --repository-names marketer \
  --query 'repositories[0].repositoryUri' --output text)

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI
docker build -t marketer:$IMAGE_TAG .
docker tag marketer:$IMAGE_TAG $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:$IMAGE_TAG

# Step 5: Deploy with real image
cd terraform/environments/prod
terraform apply -var="image_tag=$IMAGE_TAG" [other vars] -auto-approve

# Step 6: Verify
aws ecs wait services-stable --cluster marketer-prod --services marketer
curl https://$(terraform output -raw alb_dns_name)/ready

# Step 7: Register with ROUTER (coordinate with ROUTER team)
# Provide: endpoint_url, INBOUND_TOKEN, CALLBACK_API_KEY
```

---

## 9. Troubleshooting Quick Reference

| Symptom | First Check | Common Fix |
|---------|-------------|------------|
| 503 on all requests | `/ready` → "GEMINI_API_KEY not set" | Update Secrets Manager |
| 401 on POST /tasks | INBOUND_TOKEN mismatch | Rotate + sync with ROUTER |
| All tasks FAILED: TimeoutError | LLM_TIMEOUT_SECONDS | Increase to 45s |
| All tasks FAILED: ResourceExhausted | Gemini quota | Increase quota |
| High schema_repair_used rate | GEMINI_MODEL value | Test model on staging first |
| No callbacks delivered | CloudWatch: callback_failed logs | Check ORCH_CALLBACK_API_KEY |
| 0 running ECS tasks | CloudWatch task stopped reason | Check image, memory, secrets |
| High memory usage (>400MB) | Large gallery in request | Verify gallery cap at 20 |
| Slow ACK (>2s) | ALB access logs | Check VPC latency, TLS |
