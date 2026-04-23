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
  source                 = "../../modules/secrets"
  environment            = "prod"
  gemini_api_key         = var.gemini_api_key
  inbound_token          = var.inbound_token
  orch_callback_api_key  = var.orch_callback_api_key
  agentic_dispatcher_url = var.agentic_dispatcher_url
  gemini_model           = var.gemini_model
  log_level              = "INFO"
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

module "rds" {
  source                    = "../../modules/rds"
  environment               = "prod"
  vpc_id                    = var.vpc_id
  db_subnet_ids             = length(var.db_subnet_ids) > 0 ? var.db_subnet_ids : var.private_subnet_ids
  ecs_security_group_id     = ""
  bastion_security_group_id = ""
  instance_class            = "db.t4g.small"
  allocated_storage         = 20
  multi_az                  = true
  backup_retention_days     = 14
  deletion_protection       = true
  log_retention_days        = var.log_retention_days
  max_connections           = 200
  permission_boundary_arn   = var.permission_boundary_arn
}

module "bastion" {
  source                  = "../../modules/bastion"
  environment             = "prod"
  enabled                 = var.enable_bastion
  vpc_id                  = var.vpc_id
  subnet_id               = var.private_subnet_ids[0]
  rds_security_group_id   = ""
  database_url_secret_arn = module.rds.database_url_secret_arn
  permission_boundary_arn = var.permission_boundary_arn
  auto_stop_enabled       = false
}

module "ecs" {
  source = "../../modules/ecs"

  environment           = "prod"
  aws_region            = var.aws_region
  vpc_id                = var.vpc_id
  private_subnet_ids    = var.private_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  target_group_arn      = module.alb.target_group_arn

  task_execution_role_arn           = module.iam.task_execution_role_arn
  task_role_arn                     = module.iam.task_role_arn
  permission_boundary_arn           = var.permission_boundary_arn
  ecr_repository_url                = module.ecr.repository_url
  image_tag                         = var.image_tag
  gemini_api_key_secret_arn         = module.secrets.gemini_api_key_arn
  inbound_token_secret_arn          = module.secrets.inbound_token_arn
  callback_api_key_secret_arn       = module.secrets.callback_api_key_arn
  agentic_dispatcher_url_secret_arn = module.secrets.agentic_dispatcher_url_arn
  database_url_secret_arn           = module.rds.database_url_secret_arn
  rds_security_group_id             = module.rds.security_group_id
  alb_arn_suffix                    = module.alb.alb_arn_suffix
  target_group_arn_suffix           = module.alb.target_group_arn_suffix

  gemini_model            = var.gemini_model
  task_cpu                = var.task_cpu
  task_memory             = var.task_memory
  min_capacity            = var.min_capacity
  max_capacity            = var.max_capacity
  llm_timeout             = var.llm_timeout
  callback_retry_attempts = var.callback_retry_attempts
  log_retention_days      = var.log_retention_days
  db_pool_size            = var.db_pool_size
  db_pool_max_overflow    = var.db_pool_max_overflow
  db_pool_timeout_seconds = 10
  migrator_cpu            = 512
  migrator_memory         = 1024
  assign_public_ip        = false
}

module "monitoring" {
  source = "../../modules/monitoring"

  environment             = "prod"
  alert_email             = var.alert_email
  ecs_cluster_name        = module.ecs.cluster_name
  alb_arn_suffix          = module.alb.alb_arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_enabled             = true
  rds_instance_id         = module.rds.instance_id
}

resource "aws_security_group_rule" "rds_ingress_from_bastion" {
  count                    = var.enable_bastion ? 1 : 0
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = module.rds.security_group_id
  source_security_group_id = module.bastion.security_group_id
  description              = "Postgres from bastion"
}

resource "aws_security_group_rule" "rds_ingress_from_ecs" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = module.rds.security_group_id
  source_security_group_id = module.ecs.security_group_id
  description              = "Postgres from ECS"
}

resource "aws_security_group_rule" "bastion_egress_to_rds" {
  count                    = var.enable_bastion ? 1 : 0
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = module.bastion.security_group_id
  source_security_group_id = module.rds.security_group_id
  description              = "Bastion to RDS"
}
