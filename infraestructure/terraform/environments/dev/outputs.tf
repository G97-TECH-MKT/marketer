output "alb_dns_name" {
  description = "ALB DNS name"
  value       = module.alb.alb_dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_service_name" {
  value = module.ecs.service_name
}

output "log_group_name" {
  value = module.ecs.log_group_name
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "database_url_secret_arn" {
  value = module.rds.database_url_secret_arn
}

output "bastion_instance_id" {
  value = module.bastion.instance_id
}

output "migrator_task_definition_arn" {
  value = module.ecs.migrator_task_definition_arn
}
