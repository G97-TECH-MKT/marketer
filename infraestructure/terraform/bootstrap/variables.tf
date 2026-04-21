variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "S3 bucket name for Terraform remote state (must be globally unique)"
  type        = string
}

variable "github_org" {
  description = "GitHub organization or user name (e.g. 'orbidi')"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name (e.g. 'marketer')"
  type        = string
  default     = "marketer"
}