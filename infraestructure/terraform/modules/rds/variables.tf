variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for RDS security group"
  type        = string
}

variable "db_subnet_ids" {
  description = "Subnet IDs for DB subnet group"
  type        = list(string)
}

variable "ecs_security_group_id" {
  description = "Security group ID of ECS tasks"
  type        = string
  default     = ""
}

variable "bastion_security_group_id" {
  description = "Security group ID of bastion host"
  type        = string
  default     = ""
}

variable "permission_boundary_arn" {
  description = "Permission boundary ARN for IAM roles"
  type        = string
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "marketer"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "marketer"
}

variable "allocated_storage" {
  description = "Allocated storage (GB)"
  type        = number
  default     = 20
}

variable "max_allocated_storage" {
  description = "Max allocated storage (GB)"
  type        = number
  default     = 100
}

variable "multi_az" {
  description = "Whether DB should run in Multi-AZ mode"
  type        = bool
}

variable "backup_retention_days" {
  description = "Backup retention in days"
  type        = number
}

variable "deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
}

variable "log_retention_days" {
  description = "CloudWatch logs retention days"
  type        = number
  default     = 90
}

variable "max_connections" {
  description = "PostgreSQL max_connections override"
  type        = number
}
