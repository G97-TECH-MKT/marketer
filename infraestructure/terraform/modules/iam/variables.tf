variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "permission_boundary_arn" {
  description = "ARN of the MarketerPermissionBoundary policy (created in bootstrap)"
  type        = string
}
