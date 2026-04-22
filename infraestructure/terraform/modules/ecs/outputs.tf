output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.marketer.name
}

output "cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.marketer.arn
}

output "service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.marketer.name
}

output "task_definition_arn" {
  description = "Current task definition ARN"
  value       = aws_ecs_task_definition.marketer.arn
}

output "security_group_id" {
  description = "ECS tasks security group ID"
  value       = aws_security_group.ecs.id
}

output "log_group_name" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.marketer.name
}

output "migrator_task_definition_arn" {
  description = "Migrator task definition ARN"
  value       = aws_ecs_task_definition.migrator.arn
}

output "migrator_log_group_name" {
  description = "Migrator CloudWatch log group name"
  value       = aws_cloudwatch_log_group.migrator.name
}
