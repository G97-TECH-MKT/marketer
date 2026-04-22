variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "permission_boundary_arn" {
  description = "ARN of the MarketerPermissionBoundary policy (created in bootstrap)"
  type        = string
}

variable "rds_kms_key_arn" {
  description = "KMS key ARN used to encrypt RDS database-url secret"
  type        = string
  default     = ""
}
