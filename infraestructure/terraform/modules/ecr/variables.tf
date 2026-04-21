variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "create_repository" {
  description = "Create the ECR repository. Set false in dev to reuse the repo created by prod."
  type        = bool
  default     = true
}
