output "instance_id" {
  description = "Bastion instance ID"
  value       = try(aws_instance.bastion[0].id, null)
}

output "security_group_id" {
  description = "Bastion security group ID"
  value       = try(aws_security_group.bastion[0].id, null)
}

output "ssm_start_command" {
  description = "Port-forward command template for SSM"
  value = try(
    "aws ssm start-session --target ${aws_instance.bastion[0].id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters '{\"host\":[\"<rds-endpoint>\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"15432\"]}'",
    null
  )
}
