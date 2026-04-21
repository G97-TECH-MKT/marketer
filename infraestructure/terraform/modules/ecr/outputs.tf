output "repository_url" {
  description = "ECR repository URL"
  value       = local.repository_url
}

output "repository_arn" {
  description = "ECR repository ARN"
  value       = local.repository_arn
}

output "registry_id" {
  description = "ECR registry ID (AWS account ID)"
  value       = local.registry_id
}
