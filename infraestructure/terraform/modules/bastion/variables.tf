variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "enabled" {
  description = "Enable bastion resources"
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for bastion instance"
  type        = string
}

variable "rds_security_group_id" {
  description = "RDS security group ID"
  type        = string
  default     = ""
}

variable "database_url_secret_arn" {
  description = "Database URL secret ARN"
  type        = string
}

variable "permission_boundary_arn" {
  description = "Permission boundary ARN"
  type        = string
}

variable "instance_type" {
  description = "Bastion EC2 instance type"
  type        = string
  default     = "t4g.nano"
}

variable "auto_stop_enabled" {
  description = "Enable EventBridge scheduler to auto-stop bastion"
  type        = bool
  default     = false
}
