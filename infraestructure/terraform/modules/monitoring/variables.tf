variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "alert_email" {
  description = "Email for CloudWatch alarm notifications (leave empty to skip)"
  type        = string
  default     = ""
}

variable "ecs_cluster_name" {
  description = "ECS cluster name (for alarm dimensions)"
  type        = string
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix (for CloudWatch metric dimensions)"
  type        = string
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix (for CloudWatch metric dimensions)"
  type        = string
}
