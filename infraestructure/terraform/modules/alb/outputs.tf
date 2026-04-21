output "alb_arn" {
  description = "ALB ARN"
  value       = aws_lb.marketer.arn
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.marketer.dns_name
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix (used for CloudWatch metrics)"
  value       = aws_lb.marketer.arn_suffix
}

output "target_group_arn" {
  description = "Target group ARN"
  value       = aws_lb_target_group.marketer.arn
}

output "target_group_arn_suffix" {
  description = "Target group ARN suffix (used for CloudWatch metrics)"
  value       = aws_lb_target_group.marketer.arn_suffix
}

output "security_group_id" {
  description = "ALB security group ID"
  value       = aws_security_group.alb.id
}
