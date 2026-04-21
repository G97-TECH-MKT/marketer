# 05 — Terraform Reference

**Version:** 2.0  
**Last Updated:** 2026-04-21  
**Provider:** AWS  
**Terraform Version:** >= 1.6  
**AWS Provider Version:** >= 5.0

---

## 1. Repository Structure

```
terraform/
├── versions.tf                    # Provider requirements
├── variables.tf                   # Input variables
├── outputs.tf                     # Output values
├── modules/
│   ├── vpc/                       # VPC, subnets, routing
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── ecr/                       # Container registry
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── ecs/                       # ECS cluster + service
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── alb/                       # Load balancer
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── iam/                       # IAM roles
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── secrets/                   # Secrets Manager
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── rds/                       # PostgreSQL (future)
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── monitoring/                # CloudWatch
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
└── environments/
    ├── dev/
    │   ├── main.tf
    │   ├── terraform.tfvars
    │   └── backend.tf
    └── prod/
        ├── main.tf
        ├── terraform.tfvars
        └── backend.tf
```

---

## 2. Backend Configuration

### `terraform/environments/prod/backend.tf`

```hcl
terraform {
  backend "s3" {
    bucket         = "agent_marketing-terraform-state"
    key            = "marketer/prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-state-lock"
  }
}
```

### Bootstrap (run once)

```bash
# Create S3 bucket for state
aws s3api create-bucket \
  --bucket agent_marketing-terraform-state \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket agent_marketing-terraform-state \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket agent_marketing-terraform-state \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'

# Create DynamoDB table for locking
aws dynamodb create-table \
  --table-name terraform-state-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

---

## 3. versions.tf

```hcl
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "marketer"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = "platform-team"
    }
  }
}
```

---

## 4. variables.tf (Root)

```hcl
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment: dev, staging, prod"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod"
  }
}

variable "image_tag" {
  description = "Docker image tag to deploy (e.g., git SHA)"
  type        = string
}

variable "gemini_api_key" {
  description = "Google Gemini API key"
  type        = string
  sensitive   = true
}

variable "inbound_token" {
  description = "Bearer token for POST /tasks auth"
  type        = string
  sensitive   = true
}

variable "orch_callback_api_key" {
  description = "X-API-Key for PATCH callback to ROUTER"
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  description = "Gemini model identifier"
  type        = string
  default     = "gemini-2.5-flash-preview"
}

variable "min_capacity" {
  description = "Minimum ECS task count"
  type        = number
  default     = 1
}

variable "max_capacity" {
  description = "Maximum ECS task count"
  type        = number
  default     = 5
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate task memory in MB"
  type        = number
  default     = 512
}

variable "alert_email" {
  description = "Email for CloudWatch alarms (SNS)"
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Domain for the service (e.g., marketer.internal.plinng.io)"
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS"
  type        = string
  default     = ""
}
```

---

## 5. Module: VPC

### `modules/vpc/main.tf`

```hcl
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "marketer-${var.environment}" }
}

# Internet Gateway (for ALB and optional public Fargate)
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "marketer-${var.environment}-igw" }
}

# Public subnets (ALB)
resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "marketer-${var.environment}-public-${count.index + 1}" }
}

# Private subnets (ECS tasks, RDS)
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "marketer-${var.environment}-private-${count.index + 1}" }
}

# Database subnets (RDS - future)
resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 20}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "marketer-${var.environment}-db-${count.index + 1}" }
}

# Public route table
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "marketer-${var.environment}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# NAT Gateway (single AZ, cost-optimized)
# Only needed if Fargate tasks are in private subnets AND need internet egress
# Skip if using VPC Endpoints for AWS services + public subnet placement
resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"
  tags   = { Name = "marketer-${var.environment}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  count         = var.enable_nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "marketer-${var.environment}-nat" }
}

# Private route table (with NAT or without)
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  dynamic "route" {
    for_each = var.enable_nat_gateway ? [1] : []
    content {
      cidr_block     = "0.0.0.0/0"
      nat_gateway_id = aws_nat_gateway.main[0].id
    }
  }

  tags = { Name = "marketer-${var.environment}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# VPC Endpoints (replace NAT Gateway for AWS services)
locals {
  vpc_endpoint_services = [
    "com.amazonaws.${data.aws_region.current.name}.ecr.api",
    "com.amazonaws.${data.aws_region.current.name}.ecr.dkr",
    "com.amazonaws.${data.aws_region.current.name}.logs",
    "com.amazonaws.${data.aws_region.current.name}.secretsmanager",
    "com.amazonaws.${data.aws_region.current.name}.ssm",
  ]
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(local.vpc_endpoint_services)
  vpc_id              = aws_vpc.main.id
  service_name        = each.value
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "marketer-${var.environment}-${replace(each.value, ".", "-")}" }
}

# S3 Gateway Endpoint (free)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "marketer-${var.environment}-s3" }
}

# Security Group for VPC Endpoints
resource "aws_security_group" "vpc_endpoints" {
  name        = "marketer-${var.environment}-vpc-endpoints"
  description = "Allow HTTPS from ECS tasks to VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = { Name = "marketer-${var.environment}-vpc-endpoints-sg" }
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_region" "current" {}
```

---

## 6. Module: ECR

### `modules/ecr/main.tf`

```hcl
resource "aws_ecr_repository" "marketer" {
  name                 = "marketer"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = "marketer" }
}

resource "aws_ecr_lifecycle_policy" "marketer" {
  repository = aws_ecr_repository.marketer.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
```

---

## 7. Module: IAM

### `modules/iam/main.tf`

```hcl
# Task Execution Role (Fargate agent → ECR, Secrets Manager)
resource "aws_iam_role" "task_execution" {
  name = "marketer-${var.environment}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "marketer-secrets-access"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:marketer/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/marketer/*"]
      }
    ]
  })
}

# Task Role (application runtime)
resource "aws_iam_role" "task" {
  name = "marketer-${var.environment}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "task_logs" {
  name = "marketer-logs"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ]
      Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/marketer:*"
    }]
  })
}

data "aws_caller_identity" "current" {}
```

---

## 8. Module: Secrets

### `modules/secrets/main.tf`

```hcl
resource "aws_secretsmanager_secret" "gemini_api_key" {
  name        = "marketer/gemini-api-key"
  description = "Google Gemini API key for Marketer service"

  recovery_window_in_days = 7

  tags = { Component = "marketer-secrets" }
}

resource "aws_secretsmanager_secret_version" "gemini_api_key" {
  secret_id     = aws_secretsmanager_secret.gemini_api_key.id
  secret_string = var.gemini_api_key
}

resource "aws_secretsmanager_secret" "inbound_token" {
  name        = "marketer/inbound-token"
  description = "Bearer token for POST /tasks authentication"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "inbound_token" {
  secret_id     = aws_secretsmanager_secret.inbound_token.id
  secret_string = var.inbound_token
}

resource "aws_secretsmanager_secret" "callback_api_key" {
  name        = "marketer/callback-api-key"
  description = "X-API-Key for PATCH callback to ROUTER"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "callback_api_key" {
  secret_id     = aws_secretsmanager_secret.callback_api_key.id
  secret_string = var.orch_callback_api_key
}

# SSM Parameters (non-sensitive config)
resource "aws_ssm_parameter" "gemini_model" {
  name  = "/marketer/gemini-model"
  type  = "String"
  value = var.gemini_model
}

resource "aws_ssm_parameter" "log_level" {
  name  = "/marketer/log-level"
  type  = "String"
  value = var.log_level
}
```

---

## 9. Module: ALB

### `modules/alb/main.tf`

```hcl
resource "aws_lb" "marketer" {
  name               = "marketer-${var.environment}"
  internal           = var.internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = var.environment == "prod"

  access_logs {
    bucket  = var.access_log_bucket
    prefix  = "marketer-alb"
    enabled = var.environment == "prod"
  }

  tags = { Name = "marketer-${var.environment}-alb" }
}

# Security Group
resource "aws_security_group" "alb" {
  name        = "marketer-${var.environment}-alb"
  description = "ALB for Marketer service"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs  # ["0.0.0.0/0"] or internal VPC CIDR
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  egress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [var.ecs_security_group_id]
  }

  tags = { Name = "marketer-${var.environment}-alb-sg" }
}

# HTTP → HTTPS redirect
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.marketer.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# HTTPS listener
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.marketer.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.marketer.arn
  }
}

# Target Group
resource "aws_lb_target_group" "marketer" {
  name        = "marketer-${var.environment}"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = "/ready"
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  deregistration_delay = 30  # fast deregistration for rolling deploys

  tags = { Name = "marketer-${var.environment}-tg" }
}
```

---

## 10. Module: ECS

### `modules/ecs/main.tf`

```hcl
resource "aws_ecs_cluster" "marketer" {
  name = "marketer-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "marketer-${var.environment}" }
}

resource "aws_cloudwatch_log_group" "marketer" {
  name              = "/ecs/marketer"
  retention_in_days = var.log_retention_days
  tags              = { Name = "marketer-${var.environment}-logs" }
}

resource "aws_ecs_task_definition" "marketer" {
  family                   = "marketer"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "marketer"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "LOG_LEVEL",                     value = "INFO" },
        { name = "GEMINI_MODEL",                  value = var.gemini_model },
        { name = "LLM_TIMEOUT_SECONDS",           value = tostring(var.llm_timeout) },
        { name = "CALLBACK_RETRY_ATTEMPTS",       value = tostring(var.callback_retry_attempts) },
        { name = "CALLBACK_HTTP_TIMEOUT_SECONDS", value = "30.0" },
        { name = "EXTRAS_LIST_TRUNCATION",        value = "10" },
      ]

      secrets = [
        {
          name      = "GEMINI_API_KEY"
          valueFrom = var.gemini_api_key_secret_arn
        },
        {
          name      = "INBOUND_TOKEN"
          valueFrom = var.inbound_token_secret_arn
        },
        {
          name      = "ORCH_CALLBACK_API_KEY"
          valueFrom = var.callback_api_key_secret_arn
        }
      ]

      healthCheck = {
        command     = ["CMD", "curl", "-f", "http://localhost:8080/ready"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/marketer"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "marketer-${var.environment}" }
}

resource "aws_ecs_service" "marketer" {
  name                               = "marketer"
  cluster                            = aws_ecs_cluster.marketer.id
  task_definition                    = aws_ecs_task_definition.marketer.arn
  desired_count                      = var.min_capacity
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  health_check_grace_period_seconds  = 60
  force_new_deployment               = true

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "marketer"
    container_port   = 8080
  }

  deployment_controller {
    type = "ECS"
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count]  # managed by auto-scaling
  }

  tags = { Name = "marketer-${var.environment}" }
}

resource "aws_security_group" "ecs" {
  name        = "marketer-${var.environment}-ecs"
  description = "ECS tasks for Marketer service"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
    description     = "ALB only"
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS egress (Gemini API, callbacks)"
  }

  tags = { Name = "marketer-${var.environment}-ecs-sg" }
}

# Auto Scaling
resource "aws_appautoscaling_target" "marketer" {
  max_capacity       = var.max_capacity
  min_capacity       = var.min_capacity
  resource_id        = "service/${aws_ecs_cluster.marketer.name}/marketer"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "marketer_cpu" {
  name               = "marketer-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = aws_appautoscaling_target.marketer.scalable_dimension
  service_namespace  = aws_appautoscaling_target.marketer.service_namespace

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

---

## 11. Module: Monitoring

### `modules/monitoring/main.tf`

```hcl
# SNS Topic for alerts
resource "aws_sns_topic" "marketer_alerts" {
  name = "marketer-${var.environment}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.marketer_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Alarms
resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  alarm_name          = "marketer-${var.environment}-unhealthy-tasks"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 0
  alarm_actions       = [aws_sns_topic.marketer_alerts.arn]

  dimensions = {
    TargetGroup  = var.target_group_arn_suffix
    LoadBalancer = var.alb_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "high_5xx" {
  alarm_name          = "marketer-${var.environment}-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.marketer_alerts.arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

# CloudWatch Dashboard
resource "aws_cloudwatch_dashboard" "marketer" {
  dashboard_name = "marketer-${var.environment}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0; y = 0; width = 12; height = 6
        properties = {
          title   = "Request Count"
          metrics = [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix]]
          period  = 60
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 12; y = 0; width = 12; height = 6
        properties = {
          title   = "HTTP 5xx Errors"
          metrics = [["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix]]
          period  = 60
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 0; y = 6; width = 12; height = 6
        properties = {
          title   = "ECS CPU Utilization"
          metrics = [["AWS/ECS", "CPUUtilization", "ClusterName", "marketer-${var.environment}", "ServiceName", "marketer"]]
          period  = 60
          stat    = "Average"
        }
      },
      {
        type   = "metric"
        x      = 12; y = 6; width = 12; height = 6
        properties = {
          title   = "ECS Running Tasks"
          metrics = [["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", "marketer-${var.environment}", "ServiceName", "marketer"]]
          period  = 60
          stat    = "Average"
        }
      }
    ]
  })
}
```

---

## 12. Environment: Production

### `environments/prod/main.tf`

```hcl
module "vpc" {
  source              = "../../modules/vpc"
  environment         = "prod"
  enable_nat_gateway  = true
}

module "ecr" {
  source      = "../../modules/ecr"
  environment = "prod"
}

module "iam" {
  source      = "../../modules/iam"
  environment = "prod"
  aws_region  = var.aws_region
}

module "secrets" {
  source                = "../../modules/secrets"
  environment           = "prod"
  gemini_api_key        = var.gemini_api_key
  inbound_token         = var.inbound_token
  orch_callback_api_key = var.orch_callback_api_key
  gemini_model          = var.gemini_model
  log_level             = "INFO"
}

module "alb" {
  source                = "../../modules/alb"
  environment           = "prod"
  vpc_id                = module.vpc.vpc_id
  public_subnet_ids     = module.vpc.public_subnet_ids
  ecs_security_group_id = module.ecs.ecs_security_group_id
  certificate_arn       = var.certificate_arn
  allowed_cidrs         = ["10.0.0.0/8"]  # internal VPC only
  internal              = true
}

module "ecs" {
  source                      = "../../modules/ecs"
  environment                 = "prod"
  aws_region                  = var.aws_region
  ecr_repository_url          = module.ecr.repository_url
  image_tag                   = var.image_tag
  vpc_id                      = module.vpc.vpc_id
  private_subnet_ids          = module.vpc.private_subnet_ids
  task_execution_role_arn     = module.iam.task_execution_role_arn
  task_role_arn               = module.iam.task_role_arn
  target_group_arn            = module.alb.target_group_arn
  alb_security_group_id       = module.alb.security_group_id
  gemini_api_key_secret_arn   = module.secrets.gemini_api_key_arn
  inbound_token_secret_arn    = module.secrets.inbound_token_arn
  callback_api_key_secret_arn = module.secrets.callback_api_key_arn
  gemini_model                = var.gemini_model
  task_cpu                    = 256
  task_memory                 = 512
  min_capacity                = 2
  max_capacity                = 10
  llm_timeout                 = 30
  callback_retry_attempts     = 2
  log_retention_days          = 90
}

module "monitoring" {
  source               = "../../modules/monitoring"
  environment          = "prod"
  alert_email          = var.alert_email
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  alb_arn_suffix       = module.alb.alb_arn_suffix
}
```

### `environments/prod/terraform.tfvars`

```hcl
aws_region    = "us-east-1"
environment   = "prod"
image_tag     = "sha-abc123"   # set by CI/CD
gemini_model  = "gemini-2.5-flash-preview"
min_capacity  = 2
max_capacity  = 10
task_cpu      = 256
task_memory   = 512
alert_email   = "alerts@orbidi.com"
domain_name   = "marketer.internal.plinng.io"
```

---

## 13. Deployment Commands

```bash
# Initial setup
cd terraform/environments/prod
terraform init
terraform validate
terraform plan -out=tfplan

# Deploy
terraform apply tfplan

# Build & push image
export IMAGE_TAG=$(git rev-parse --short HEAD)
export ECR_URI=$(terraform output -raw ecr_repository_url)

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI

docker build -t marketer:$IMAGE_TAG .
docker tag marketer:$IMAGE_TAG $ECR_URI:$IMAGE_TAG
docker tag marketer:$IMAGE_TAG $ECR_URI:latest
docker push $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:latest

# Deploy new image
terraform apply -var="image_tag=$IMAGE_TAG" -auto-approve

# Force rolling restart (no code change needed)
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --force-new-deployment \
  --region us-east-1

# Rollback to previous task definition
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --task-definition marketer:$(( $(aws ecs describe-services \
    --cluster marketer-prod \
    --services marketer \
    --query 'services[0].taskDefinition' \
    --output text | cut -d: -f7) - 1 ))
```

---

## 14. Secrets Rotation

```bash
# Rotate GEMINI_API_KEY
aws secretsmanager put-secret-value \
  --secret-id marketer/gemini-api-key \
  --secret-string "new-api-key-here"

# Force task replacement to pick up new secret
aws ecs update-service \
  --cluster marketer-prod \
  --service marketer \
  --force-new-deployment

# Verify
aws ecs describe-services \
  --cluster marketer-prod \
  --services marketer \
  --query 'services[0].deployments'
```

---

## 15. Destroy (with safety)

```bash
# Dev environment only - NEVER run on prod without confirmation
cd terraform/environments/dev
terraform destroy -target=module.ecs    # remove service first
terraform destroy -target=module.alb    # then ALB
terraform destroy                        # then everything else
```

> **Production destroy requires:**
> - `deletion_protection = false` on RDS (if enabled)
> - ALB deletion protection disabled
> - Manual confirmation with environment name
