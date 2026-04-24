variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

# ─── Existing Network ─────────────────────────────────────────────────────────

variable "vpc_id" {
  description = "ID of the existing VPC"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the existing VPC"
  type        = string
}

variable "public_subnet_ids" {
  description = "Existing public subnet IDs for the ALB (minimum 2, across 2 AZs)"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Existing private subnet IDs for ECS tasks (minimum 2, across 2 AZs)"
  type        = list(string)
}

variable "db_subnet_ids" {
  description = "Optional dedicated DB subnet IDs. Empty means reuse private_subnet_ids."
  type        = list(string)
  default     = []
}

# ─── TLS / DNS ────────────────────────────────────────────────────────────────

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "alb_internal" {
  description = "Deploy ALB as internal (true) or internet-facing (false)"
  type        = bool
  default     = true
}

variable "alb_allowed_cidrs" {
  description = "CIDRs allowed to reach the ALB"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

# ─── Container Image ──────────────────────────────────────────────────────────

variable "image_tag" {
  description = "Docker image tag to deploy (set by CI/CD, e.g. sha-abc123)"
  type        = string
  default     = "latest"
}

# ─── Secrets (sensitive — use .tfvars or environment variables, never commit) ──

variable "gemini_api_key" {
  description = "Google Gemini API key"
  type        = string
  sensitive   = true
}

variable "inbound_token" {
  description = "Bearer token for POST /tasks authentication"
  type        = string
  sensitive   = true
}

variable "orch_callback_api_key" {
  description = "X-API-Key for PATCH callback to ROUTER"
  type        = string
  sensitive   = true
}

variable "agentic_dispatcher_url" {
  description = "Agentic dispatcher URL"
  type        = string
}

# ─── App Config ───────────────────────────────────────────────────────────────

variable "gemini_model" {
  description = "Gemini model identifier"
  type        = string
  default     = "gemini-2.5-flash-preview"
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

variable "min_capacity" {
  description = "Minimum number of ECS tasks"
  type        = number
  default     = 2
}

variable "max_capacity" {
  description = "Maximum number of ECS tasks"
  type        = number
  default     = 10
}

variable "llm_timeout" {
  description = "LLM call timeout in seconds"
  type        = number
  default     = 180
}

variable "llm_fanout_enabled" {
  description = "Enable fan-out (1xN -> Nx1) for subscription_strategy LLM batches"
  type        = bool
  default     = true
}

variable "llm_fanout_concurrency" {
  description = "Max parallel single-job LLM calls inside the fan-out semaphore"
  type        = number
  default     = 5
}

variable "orch_api_base_url" {
  description = "Orchestrator base URL for POST /api/v1/jobs (subscription_strategy fan-out dispatch). Empty → sub-job dispatch to orchestrator is skipped."
  type        = string
  default     = ""
}

variable "orch_api_http_timeout_seconds" {
  description = "HTTP timeout (seconds) for POST /api/v1/jobs calls to the orchestrator"
  type        = number
  default     = 15
}

variable "callback_retry_attempts" {
  description = "Callback retry attempts"
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 90
}

variable "alert_email" {
  description = "Email for CloudWatch alarm notifications"
  type        = string
  default     = ""
}

variable "enable_bastion" {
  description = "Enable bastion host"
  type        = bool
  default     = true
}

variable "db_pool_size" {
  description = "SQLAlchemy DB pool size"
  type        = number
  default     = 10
}

variable "db_pool_max_overflow" {
  description = "SQLAlchemy DB pool max overflow"
  type        = number
  default     = 5
}

# ─── IAM ─────────────────────────────────────────────────────────────────────

variable "permission_boundary_arn" {
  description = "ARN of MarketerPermissionBoundary (created in bootstrap)"
  type        = string
}
