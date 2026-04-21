# ─── Lookup existing VPC (validation only) ───────────────────────────────────

data "aws_vpc" "existing" {
  id = var.vpc_id
}

# ─── Modules ──────────────────────────────────────────────────────────────────

module "ecr" {
  source      = "../../modules/ecr"
  environment = "prod"
}

module "iam" {
  source                  = "../../modules/iam"
  environment             = "prod"
  permission_boundary_arn = var.permission_boundary_arn
}

module "secrets" {
  source                = "../../modules/secrets"
  environment           = "prod"
  gemini_api_key        = var.gemini_api_key
  inbound_token         = var.inbound_token
  orch_callback_api_key = var.orch_callback_api_key
  gemini_model          = var.gemini_model
  log_level             = "INFO"
}

module "alb" {
  source            = "../../modules/alb"
  environment       = "prod"
  vpc_id            = var.vpc_id
  vpc_cidr          = var.vpc_cidr
  public_subnet_ids = var.public_subnet_ids
  certificate_arn   = var.certificate_arn
  internal          = var.alb_internal
  allowed_cidrs     = var.alb_allowed_cidrs
}

module "ecs" {
  source = "../../modules/ecs"

  environment    = "prod"
  aws_region     = var.aws_region
  vpc_id         = var.vpc_id
  private_subnet_ids    = var.private_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  target_group_arn      = module.alb.target_group_arn

  task_execution_role_arn     = module.iam.task_execution_role_arn
  task_role_arn               = module.iam.task_role_arn
  ecr_repository_url          = module.ecr.repository_url
  image_tag                   = var.image_tag
  gemini_api_key_secret_arn   = module.secrets.gemini_api_key_arn
  inbound_token_secret_arn    = module.secrets.inbound_token_arn
  callback_api_key_secret_arn = module.secrets.callback_api_key_arn

  gemini_model            = var.gemini_model
  task_cpu                = var.task_cpu
  task_memory             = var.task_memory
  min_capacity            = var.min_capacity
  max_capacity            = var.max_capacity
  llm_timeout             = var.llm_timeout
  callback_retry_attempts = var.callback_retry_attempts
  log_retention_days      = var.log_retention_days
  assign_public_ip        = false
}

module "monitoring" {
  source = "../../modules/monitoring"

  environment             = "prod"
  alert_email             = var.alert_email
  ecs_cluster_name        = module.ecs.cluster_name
  alb_arn_suffix          = module.alb.alb_arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
}
