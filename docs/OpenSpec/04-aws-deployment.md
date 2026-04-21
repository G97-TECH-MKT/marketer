# 04 — AWS Deployment Architecture

**Version:** 2.0  
**Last Updated:** 2026-04-21  
**Target:** Production-grade, economical, Terraform-deployable

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AWS Account                                  │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    VPC (10.0.0.0/16)                         │    │
│  │                                                               │    │
│  │  Public Subnets (10.0.1.0/24, 10.0.2.0/24)                  │    │
│  │  ┌──────────────────────────────────────────────────────┐   │    │
│  │  │              Application Load Balancer               │   │    │
│  │  │         HTTPS :443 → HTTP :8080 (internal)           │   │    │
│  │  │              ACM Certificate (TLS)                   │   │    │
│  │  └──────────────────────┬───────────────────────────────┘   │    │
│  │                         │                                     │    │
│  │  Private Subnets (10.0.10.0/24, 10.0.11.0/24)               │    │
│  │  ┌──────────────────────▼───────────────────────────────┐   │    │
│  │  │              ECS Fargate Cluster                     │   │    │
│  │  │                                                       │   │    │
│  │  │  ┌─────────────┐  ┌─────────────┐                   │   │    │
│  │  │  │  Task 1     │  │  Task 2     │  (auto-scaled)    │   │    │
│  │  │  │  marketer   │  │  marketer   │                   │   │    │
│  │  │  │  0.25 vCPU  │  │  0.25 vCPU  │                   │   │    │
│  │  │  │  512 MB     │  │  512 MB     │                   │   │    │
│  │  │  └──────┬──────┘  └──────┬──────┘                   │   │    │
│  │  └─────────┼────────────────┼────────────────────────── ┘   │    │
│  │            │                │                                 │    │
│  │  ┌─────────▼────────────────▼────────────────────────────┐  │    │
│  │  │           VPC Endpoints (no NAT Gateway!)             │  │    │
│  │  │  • ECR API / ECR DKR (image pulls)                    │  │    │
│  │  │  • Secrets Manager (GEMINI_API_KEY, tokens)           │  │    │
│  │  │  • CloudWatch Logs (log streaming)                    │  │    │
│  │  │  • SSM (parameter store)                              │  │    │
│  │  └───────────────────────────────────────────────────────┘  │    │
│  │                                                               │    │
│  │  ┌───────────────────────────────────────────────────────┐  │    │
│  │  │  RDS PostgreSQL (future, t3.micro, Multi-AZ off dev)  │  │    │
│  │  └───────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ECR Repository          CloudWatch          Secrets Manager         │
│  (container images)      (logs, metrics,     (GEMINI_API_KEY,        │
│                           alarms, dashboard)  INBOUND_TOKEN, etc.)   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘

External:
  Google Gemini API (HTTPS egress via NAT or Internet Gateway)
  ROUTER service (internal VPC or peered VPC)
```

---

## 2. Service Choices & Rationale

### 2.1 ECS Fargate (not EKS, not EC2)

| Option | Cost | Complexity | Fit |
|--------|------|------------|-----|
| **ECS Fargate** | Pay-per-task-second | Low (managed) | ✅ Recommended |
| EKS | Fixed control plane ~$73/mo | High (K8s ops) | ❌ Overkill for single service |
| EC2 Auto Scaling | Idle costs ~$15-30/mo min | Medium | ❌ Wasteful for variable traffic |
| Lambda | 15s hard limit + cold start | Low | ❌ 12s LLM call is too risky |

**Rationale:** Fargate charges only for running task seconds. At 2 tasks × 0.25 vCPU × 512 MB, monthly cost is ~$15–30. Scales to 0 in dev. No cluster management.

### 2.2 ALB (not NLB, not API Gateway)

**ALB chosen because:**
- L7 routing — path-based rules, health checks on `/ready`
- Native ECS target group integration
- Sticky sessions (not needed here, but available)
- WAF integration (security add-on)

**API Gateway rejected because:**
- Long-lived connections (18s+ for p95) near 29s proxy timeout
- Adds $3.50/million request cost
- Adds 10–50ms overhead

### 2.3 VPC Endpoints (not NAT Gateway)

This is the **biggest cost optimization** in the architecture.

| Option | Monthly Cost |
|--------|-------------|
| NAT Gateway (1 AZ) | ~$35 + data charges |
| **VPC Endpoints** | ~$7–10 (interface endpoints × hours) |

VPC Endpoints needed for Fargate tasks in private subnets:
- `com.amazonaws.{region}.ecr.api` — Docker image manifests
- `com.amazonaws.{region}.ecr.dkr` — Docker image layers
- `com.amazonaws.{region}.logs` — CloudWatch Logs
- `com.amazonaws.{region}.secretsmanager` — Secrets Manager
- `com.amazonaws.{region}.ssm` — Parameter Store

**Gemini API egress** still requires a NAT Gateway or public subnet placement. Recommendation: place Fargate tasks in a **public subnet with private IP** (no public IP assigned), or use a single small NAT Gateway.

> **Cost decision:** If budget is very tight, run Fargate tasks in public subnets (`assign_public_ip = ENABLED`) and use VPC Endpoints only for ECR/Secrets Manager. Gemini traffic flows via Internet Gateway (no NAT Gateway cost). Trade-off: slightly reduced security posture.

### 2.4 Secrets Manager (not SSM Parameter Store, not env vars)

| Option | Cost | Security |
|--------|------|----------|
| **Secrets Manager** | $0.40/secret/month | ✅ Rotation, audit, KMS |
| SSM SecureString | $0.05/parameter/month | Good (no auto-rotation) |
| Task definition env | Free | ❌ Visible in console/API |
| Dockerfile ENV | Free | ❌ Baked into image |

Secrets Manager for: `GEMINI_API_KEY`, `INBOUND_TOKEN`, `ORCH_CALLBACK_API_KEY`, `DATABASE_URL`

SSM Parameter Store (free tier) for: `GEMINI_MODEL`, `LOG_LEVEL`, `LLM_TIMEOUT_SECONDS`

### 2.5 ECR (not DockerHub, not GitHub Packages)

ECR is in the same AWS account — no cross-cloud auth, no external dependency, native IAM integration. Cost: ~$0.10/GB/month. Image size ~200MB → ~$0.02/month.

---

## 3. Network Architecture

### 3.1 VPC Layout

```
VPC CIDR: 10.0.0.0/16

Public Subnets (AZ-A, AZ-B):
  10.0.1.0/24  (us-east-1a)   ← ALB
  10.0.2.0/24  (us-east-1b)   ← ALB

Private Subnets (AZ-A, AZ-B):
  10.0.10.0/24 (us-east-1a)   ← ECS tasks, RDS
  10.0.11.0/24 (us-east-1b)   ← ECS tasks, RDS

Database Subnets (AZ-A, AZ-B):
  10.0.20.0/24 (us-east-1a)   ← RDS (future)
  10.0.21.0/24 (us-east-1b)   ← RDS (future)
```

### 3.2 Security Groups

**ALB Security Group (`sg-alb`):**
```
Inbound:
  HTTPS 443  from 0.0.0.0/0   (public traffic)
  HTTP  80   from 0.0.0.0/0   (redirect to HTTPS)
Outbound:
  TCP 8080   to sg-ecs
```

**ECS Tasks Security Group (`sg-ecs`):**
```
Inbound:
  TCP 8080   from sg-alb       (ALB only)
Outbound:
  HTTPS 443  to 0.0.0.0/0     (Gemini API, external callbacks)
  HTTPS 443  to VPC Endpoints  (ECR, Secrets Manager, CloudWatch)
  TCP 5432   to sg-rds         (PostgreSQL, future)
```

**RDS Security Group (`sg-rds`, future):**
```
Inbound:
  TCP 5432   from sg-ecs
Outbound:
  (none needed)
```

---

## 4. IAM Roles

### 4.1 ECS Task Execution Role

Used by Fargate to pull images and fetch secrets at startup.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:{region}:{account}:secret:marketer/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:{region}:{account}:log-group:/ecs/marketer:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:{region}:{account}:parameter/marketer/*"
    }
  ]
}
```

### 4.2 ECS Task Role

Used by the application at runtime (minimal permissions).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:{region}:{account}:log-group:/ecs/marketer:*"
    }
  ]
}
```

> **Principle of least privilege:** The task role has no S3, DynamoDB, SQS, or other permissions. Add only what's needed when persistence is implemented.

---

## 5. Container Configuration

### 5.1 ECS Task Definition

```json
{
  "family": "marketer",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::{account}:role/marketer-task-execution-role",
  "taskRoleArn": "arn:aws:iam::{account}:role/marketer-task-role",
  "containerDefinitions": [
    {
      "name": "marketer",
      "image": "{account}.dkr.ecr.{region}.amazonaws.com/marketer:latest",
      "portMappings": [
        {"containerPort": 8080, "protocol": "tcp"}
      ],
      "environment": [
        {"name": "LOG_LEVEL", "value": "INFO"},
        {"name": "GEMINI_MODEL", "value": "gemini-2.5-flash-preview"},
        {"name": "LLM_TIMEOUT_SECONDS", "value": "30"},
        {"name": "CALLBACK_RETRY_ATTEMPTS", "value": "2"}
      ],
      "secrets": [
        {
          "name": "GEMINI_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:{region}:{account}:secret:marketer/gemini-api-key"
        },
        {
          "name": "INBOUND_TOKEN",
          "valueFrom": "arn:aws:secretsmanager:{region}:{account}:secret:marketer/inbound-token"
        },
        {
          "name": "ORCH_CALLBACK_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:{region}:{account}:secret:marketer/callback-api-key"
        }
      ],
      "healthCheck": {
        "command": ["CMD", "curl", "-f", "http://localhost:8080/ready"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 15
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/marketer",
          "awslogs-region": "{region}",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "essential": true
    }
  ]
}
```

### 5.2 Sizing Rationale

| Resource | Allocated | Typical Usage | Headroom |
|----------|-----------|--------------|---------|
| CPU | 0.25 vCPU | ~0.02 vCPU | 12x |
| Memory | 512 MB | ~100 MB | 5x |

The service is almost entirely I/O-bound (waiting on Gemini). CPU and memory allocations are minimal. If concurrent task count grows (>20 per replica), consider bumping to 0.5 vCPU / 1 GB.

---

## 6. Auto Scaling

### 6.1 Target Tracking Policy

```hcl
resource "aws_appautoscaling_policy" "marketer_cpu" {
  name               = "marketer-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"

  target_tracking_scaling_policy_configuration {
    target_value       = 70.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
```

### 6.2 Scaling Limits

| Environment | Min Tasks | Max Tasks |
|-------------|-----------|-----------|
| dev | 0 | 2 |
| staging | 1 | 4 |
| prod | 2 | 10 |

**Note:** Min=0 for dev saves cost during off-hours. Cold start for Fargate Fargate is ~15–30s (image pull cached after first).

### 6.3 Custom Scaling Metric (Optional)

For more accurate scaling (this service is network-bound, not CPU-bound), use a custom CloudWatch metric based on active background tasks. This requires the app to emit a `ActiveTasks` metric:

```python
# In reasoner.py, wrap background task counter with CloudWatch metric
cloudwatch.put_metric_data(
    Namespace='Marketer',
    MetricData=[{'MetricName': 'ActiveTasks', 'Value': active_count, 'Unit': 'Count'}]
)
```

---

## 7. Deployment Pipeline (CI/CD)

```
GitHub Push (main)
    │
    ├─ GitHub Actions workflow
    │
    ├─ 1. Test
    │      pytest (offline)
    │      mypy type check
    │
    ├─ 2. Build
    │      docker build -t marketer:{sha} .
    │      docker tag marketer:{sha} {ecr_uri}:latest
    │      docker tag marketer:{sha} {ecr_uri}:{sha}
    │
    ├─ 3. Push to ECR
    │      aws ecr get-login-password | docker login
    │      docker push {ecr_uri}:latest
    │      docker push {ecr_uri}:{sha}
    │
    └─ 4. Deploy
           aws ecs update-service
             --cluster marketer-{env}
             --service marketer
             --force-new-deployment
```

**Deployment strategy:** Rolling update with minimum 50% healthy. With min=2 tasks, Fargate replaces one task at a time.

**Rollback:** `aws ecs update-service --task-definition marketer:{prev_revision}` or via Terraform with pinned image tag.

---

## 8. Cost Model

### 8.1 Dev Environment

| Service | Config | Monthly |
|---------|--------|---------|
| ECS Fargate | 0 tasks (scaled to 0 nights/weekends) | ~$5 |
| ALB | 1 ALB, ~10k requests/day | ~$20 |
| ECR | 1 repo, 200 MB | ~$0.02 |
| CloudWatch | Logs 1 GB/month | ~$0.50 |
| Secrets Manager | 4 secrets | ~$1.60 |
| VPC Endpoints | 4 interface endpoints × $0.01/hr | ~$30 |
| **Total** | | **~$57/month** |

> Optimize: Use NAT Gateway ($35 fixed) instead of VPC Endpoints if running few tasks. Break-even is at ~3.5 interface endpoints.

### 8.2 Production Environment

| Service | Config | Monthly |
|---------|--------|---------|
| ECS Fargate | 2 tasks × 0.25 vCPU × 512 MB (24/7) | ~$30 |
| ALB | 1 ALB, ~100k requests/day | ~$25 |
| ECR | 1 repo, 200 MB, 10 GB data transfer | ~$1 |
| CloudWatch | Logs 10 GB/month + 5 alarms + 1 dashboard | ~$8 |
| Secrets Manager | 4 secrets | ~$1.60 |
| VPC Endpoints | 4 endpoints | ~$30 |
| ACM | Free (public cert) | $0 |
| Route53 | 1 hosted zone | ~$0.50 |
| **Total** | | **~$96/month** |

### 8.3 High-Traffic Scaling

At 10 tasks (max scale), add ~$150/month in Fargate. Gemini costs dominate at volume:

| Requests/day | Gemini cost/month | Fargate | Total |
|--------------|------------------|---------|-------|
| 1,000 | ~$3 | ~$30 | ~$130 |
| 10,000 | ~$30 | ~$60 | ~$190 |
| 100,000 | ~$300 | ~$200 | ~$600 |

---

## 9. DNS & TLS

```
Route53 Hosted Zone: plinng.io (or your domain)
  A record: marketer.internal.plinng.io → ALB DNS name

ACM Certificate: *.internal.plinng.io (wildcard)
  Validation: DNS validation (automatic with Terraform)
  Attached to: ALB HTTPS listener
```

For internal-only access (no public DNS needed), use ALB internal scheme with private hosted zone:

```hcl
resource "aws_lb" "marketer" {
  internal           = true
  load_balancer_type = "application"
  ...
}
```

---

## 10. Observability Stack

### 10.1 CloudWatch Logs

```
Log Group: /ecs/marketer
Retention: 30 days (dev), 90 days (prod)
Format: JSON (structured)
```

**Key log queries (CloudWatch Insights):**

```sql
-- Failed tasks in last 1h
fields @timestamp, task_id, error_message
| filter status = "FAILED"
| sort @timestamp desc
| limit 100

-- p95 latency in last 24h
fields latency_ms
| filter ispresent(latency_ms)
| stats pct(latency_ms, 95) as p95, avg(latency_ms) as avg by bin(1h)

-- Warning distribution
fields warnings.0.code
| filter ispresent(warnings.0.code)
| stats count(*) as n by warnings.0.code
| sort n desc
```

### 10.2 CloudWatch Metrics & Alarms

| Alarm | Metric | Threshold | Action |
|-------|--------|-----------|--------|
| High error rate | `FAILED` callbacks / total | >5% over 5min | SNS → PagerDuty |
| High latency | p95 latency_ms | >25000 ms | SNS → Slack |
| Gemini timeout rate | `internal_error: TimeoutError` count | >3 in 5min | SNS → Slack |
| Schema repair rate | `schema_repair_used` count | >10 in 15min | SNS → Slack |
| Container health | ECS `UnhealthyTaskCount` | >0 | SNS → PagerDuty |
| CPU high | ECS `CPUUtilization` | >80% | Auto-scale trigger |

### 10.3 Dashboard

CloudWatch Dashboard: `marketer-{env}`

Widgets:
1. Request rate (POST /tasks per minute)
2. COMPLETED vs FAILED ratio
3. p50/p95/p99 latency (from trace.latency_ms)
4. Warning distribution (pie chart)
5. ECS task count
6. CPU and memory utilization
7. ALB request count + 4xx/5xx rates
8. Gemini API timeout count

---

## 11. Backup & Recovery

### 11.1 Stateless Service

Marketer is currently **fully stateless**. No backup strategy needed for the service itself.

- Configuration is in Secrets Manager (AWS-managed durability)
- Container images are in ECR (replicated across AZs)
- Logs are in CloudWatch (configurable retention)

### 11.2 PostgreSQL (When Enabled)

```hcl
resource "aws_db_instance" "marketer" {
  backup_retention_period = 7        # 7 days automated backups
  backup_window           = "03:00-04:00"
  multi_az                = false    # dev; true for prod
  deletion_protection     = true     # prod
  skip_final_snapshot     = false
  final_snapshot_identifier = "marketer-final-${timestamp()}"
}
```

**RPO:** 5 minutes (with enabled_cloudwatch_logs_exports)  
**RTO:** ~5 minutes (single-AZ restore) / ~1 minute (Multi-AZ failover)
