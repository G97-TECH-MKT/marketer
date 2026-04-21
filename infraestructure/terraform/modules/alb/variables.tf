variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "vpc_id" {
  description = "ID of the existing VPC"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the existing VPC (used for ALB egress SG rule)"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for the ALB"
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
}

variable "internal" {
  description = "true = internal ALB (private); false = internet-facing"
  type        = bool
  default     = true
}

variable "allowed_cidrs" {
  description = "CIDR blocks allowed to reach the ALB (e.g. VPC CIDR for internal)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
