output "gemini_api_key_arn" {
  description = "ARN of the Gemini API key secret"
  value       = aws_secretsmanager_secret.gemini_api_key.arn
}

output "inbound_token_arn" {
  description = "ARN of the inbound token secret"
  value       = aws_secretsmanager_secret.inbound_token.arn
}

output "callback_api_key_arn" {
  description = "ARN of the callback API key secret"
  value       = aws_secretsmanager_secret.callback_api_key.arn
}

output "agentic_dispatcher_url_arn" {
  description = "ARN of the agentic dispatcher URL secret"
  value       = aws_secretsmanager_secret.agentic_dispatcher_url.arn
}
