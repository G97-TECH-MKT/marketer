output "state_bucket_name" {
  description = "S3 bucket for Terraform state"
  value       = aws_s3_bucket.tf_state.id
}

output "dynamodb_lock_table" {
  description = "DynamoDB table for Terraform state locking"
  value       = aws_dynamodb_table.tf_locks.name
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC deployment"
  value       = aws_iam_role.github_actions.arn
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN"
  value       = aws_iam_openid_connect_provider.github.arn
}

output "permission_boundary_arn" {
  description = "Permission boundary ARN to attach to marketer IAM roles"
  value       = aws_iam_policy.marketer_boundary.arn
}
