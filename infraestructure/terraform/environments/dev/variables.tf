variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "ID of the existing VPC"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the existing VPC"
  type        = string
}

variable "public_subnet_ids" {
  description = "Existing public subnet IDs for the ALB"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Existing subnet IDs for ECS tasks (public subnets OK for dev with assign_public_ip=true)"
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}

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

variable "gemini_model" {
  description = "Gemini model identifier"
  type        = string
  default     = "gemini-2.5-flash-preview"
}

variable "permission_boundary_arn" {
  description = "ARN of MarketerPermissionBoundary (created in bootstrap)"
  type        = string
}

variable "assign_public_ip" {
  description = "Assign public IP to Fargate tasks (true = no NAT needed for dev)"
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email for CloudWatch alarm notifications"
  type        = string
  default     = ""
}
