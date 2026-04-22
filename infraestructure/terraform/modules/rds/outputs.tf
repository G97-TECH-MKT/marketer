output "instance_id" {
  description = "RDS instance identifier"
  value       = aws_db_instance.marketer.identifier
}

output "endpoint" {
  description = "RDS endpoint"
  value       = aws_db_instance.marketer.address
}

output "port" {
  description = "RDS port"
  value       = aws_db_instance.marketer.port
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.marketer.db_name
}

output "security_group_id" {
  description = "RDS security group ID"
  value       = aws_security_group.rds.id
}

output "database_url_secret_arn" {
  description = "Secret ARN containing database URL"
  value       = aws_secretsmanager_secret.database_url.arn
}

output "kms_key_arn" {
  description = "KMS key ARN used by RDS and DB secret"
  value       = aws_kms_key.rds.arn
}
