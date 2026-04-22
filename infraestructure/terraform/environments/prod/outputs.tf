output "alb_dns_name" {
  description = "ALB DNS name — create a CNAME/Alias record pointing to this"
  value       = module.alb.alb_dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL (use as base for docker push)"
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = module.ecs.cluster_name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = module.ecs.service_name
}

output "log_group_name" {
  description = "CloudWatch log group"
  value       = module.ecs.log_group_name
}

output "cloudwatch_dashboard" {
  description = "CloudWatch dashboard name"
  value       = module.monitoring.dashboard_name
}

output "rds_endpoint" {
  description = "RDS endpoint"
  value       = module.rds.endpoint
}

output "database_url_secret_arn" {
  description = "Database URL secret ARN"
  value       = module.rds.database_url_secret_arn
}

output "bastion_instance_id" {
  description = "Bastion instance ID"
  value       = module.bastion.instance_id
}

output "migrator_task_definition_arn" {
  description = "Migrator task definition ARN"
  value       = module.ecs.migrator_task_definition_arn
}
