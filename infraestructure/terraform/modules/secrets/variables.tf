variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "gemini_api_key" {
  description = "Google Gemini API key"
  type        = string
  sensitive   = true
}

variable "inbound_token" {
  description = "Bearer token for POST /tasks authentication"
  type        = string
  sensitive   = true
}

variable "orch_callback_api_key" {
  description = "X-API-Key for PATCH callback to ROUTER"
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  description = "Gemini model identifier"
  type        = string
  default     = "gemini-2.5-flash-preview"
}

variable "log_level" {
  description = "Application log level"
  type        = string
  default     = "INFO"
}
