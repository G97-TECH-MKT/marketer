variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "ID of the existing VPC"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for ECS tasks"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group ID of the ALB (for ingress rule)"
  type        = string
}

variable "target_group_arn" {
  description = "ALB target group ARN"
  type        = string
}

variable "task_execution_role_arn" {
  description = "ARN of the ECS task execution role"
  type        = string
}

variable "task_role_arn" {
  description = "ARN of the ECS task role (runtime)"
  type        = string
}

variable "permission_boundary_arn" {
  description = "Permission boundary ARN for IAM roles created by this module"
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL (without tag)"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy (e.g. git SHA)"
  type        = string
}

variable "gemini_api_key_secret_arn" {
  description = "Secrets Manager ARN for GEMINI_API_KEY"
  type        = string
}

variable "inbound_token_secret_arn" {
  description = "Secrets Manager ARN for INBOUND_TOKEN"
  type        = string
}

variable "callback_api_key_secret_arn" {
  description = "Secrets Manager ARN for ORCH_CALLBACK_API_KEY"
  type        = string
}

variable "agentic_dispatcher_url_secret_arn" {
  description = "Secrets Manager ARN for AGENTIC_DISPATCHER_URL"
  type        = string
}

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
  default     = 1
}

variable "max_capacity" {
  description = "Maximum number of ECS tasks"
  type        = number
  default     = 5
}

variable "llm_timeout" {
  description = "LLM timeout in seconds"
  type        = number
  default     = 30
}

variable "callback_retry_attempts" {
  description = "Number of callback retry attempts"
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "assign_public_ip" {
  description = "Assign public IP to Fargate tasks (true for dev without NAT)"
  type        = bool
  default     = false
}

variable "rds_security_group_id" {
  description = "RDS security group ID for Postgres egress rule"
  type        = string
}

variable "database_url_secret_arn" {
  description = "Secrets Manager ARN with database URL JSON"
  type        = string
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

variable "db_pool_timeout_seconds" {
  description = "SQLAlchemy DB pool timeout seconds"
  type        = number
  default     = 10
}

variable "migrator_cpu" {
  description = "CPU for migrator one-off task"
  type        = number
  default     = 512
}

variable "migrator_memory" {
  description = "Memory for migrator one-off task"
  type        = number
  default     = 1024
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix used by ALBRequestCountPerTarget"
  type        = string
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix used by ALBRequestCountPerTarget"
  type        = string
}
