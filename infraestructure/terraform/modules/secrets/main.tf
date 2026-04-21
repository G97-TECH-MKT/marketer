# ─── Secrets Manager (sensitive credentials) ─────────────────────────────────

resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "marketer/${var.environment}/gemini-api-key"
  description             = "Google Gemini API key for Marketer ${var.environment}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "gemini_api_key" {
  secret_id     = aws_secretsmanager_secret.gemini_api_key.id
  secret_string = var.gemini_api_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "inbound_token" {
  name                    = "marketer/${var.environment}/inbound-token"
  description             = "Bearer token for POST /tasks — ${var.environment}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "inbound_token" {
  secret_id     = aws_secretsmanager_secret.inbound_token.id
  secret_string = var.inbound_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "callback_api_key" {
  name                    = "marketer/${var.environment}/callback-api-key"
  description             = "X-API-Key for PATCH callback to ROUTER — ${var.environment}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "callback_api_key" {
  secret_id     = aws_secretsmanager_secret.callback_api_key.id
  secret_string = var.orch_callback_api_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ─── SSM Parameter Store (non-sensitive config) ───────────────────────────────

resource "aws_ssm_parameter" "gemini_model" {
  name  = "/marketer/${var.environment}/gemini-model"
  type  = "String"
  value = var.gemini_model
}

resource "aws_ssm_parameter" "log_level" {
  name  = "/marketer/${var.environment}/log-level"
  type  = "String"
  value = var.log_level
}
