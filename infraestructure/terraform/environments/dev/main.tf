data "aws_vpc" "existing" {
  id = var.vpc_id
}

module "ecr" {
  source            = "../../modules/ecr"
  environment       = "dev"
  create_repository = false # Reuses the repo created by the prod environment
}

module "iam" {
  source                  = "../../modules/iam"
  environment             = "dev"
  permission_boundary_arn = var.permission_boundary_arn
}

module "secrets" {
  source                = "../../modules/secrets"
  environment           = "dev"
  gemini_api_key        = var.gemini_api_key
  inbound_token         = var.inbound_token
  orch_callback_api_key = var.orch_callback_api_key
  gemini_model          = var.gemini_model
  log_level             = "DEBUG"
}

module "alb" {
  source            = "../../modules/alb"
  environment       = "dev"
  vpc_id            = var.vpc_id
  vpc_cidr          = var.vpc_cidr
  public_subnet_ids = var.public_subnet_ids
  certificate_arn   = var.certificate_arn
  internal          = false
  allowed_cidrs     = ["0.0.0.0/0"]
}

module "rds" {
  source                    = "../../modules/rds"
  environment               = "dev"
  vpc_id                    = var.vpc_id
  db_subnet_ids             = length(var.db_subnet_ids) > 0 ? var.db_subnet_ids : var.private_subnet_ids
  ecs_security_group_id     = ""
  bastion_security_group_id = ""
  instance_class            = "db.t4g.micro"
  allocated_storage         = 20
  multi_az                  = false
  backup_retention_days     = 7
  deletion_protection       = false
  log_retention_days        = 7
  max_connections           = 100
  permission_boundary_arn   = var.permission_boundary_arn
}

module "bastion" {
  source                  = "../../modules/bastion"
  environment             = "dev"
  enabled                 = var.enable_bastion
  vpc_id                  = var.vpc_id
  subnet_id               = var.private_subnet_ids[0]
  rds_security_group_id   = ""
  database_url_secret_arn = module.rds.database_url_secret_arn
  permission_boundary_arn = var.permission_boundary_arn
  auto_stop_enabled       = true
}

module "ecs" {
  source = "../../modules/ecs"

  environment           = "dev"
  aws_region            = var.aws_region
  vpc_id                = var.vpc_id
  private_subnet_ids    = var.private_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  target_group_arn      = module.alb.target_group_arn

  task_execution_role_arn     = module.iam.task_execution_role_arn
  task_role_arn               = module.iam.task_role_arn
  permission_boundary_arn     = var.permission_boundary_arn
  ecr_repository_url          = module.ecr.repository_url
  image_tag                   = var.image_tag
  gemini_api_key_secret_arn   = module.secrets.gemini_api_key_arn
  inbound_token_secret_arn    = module.secrets.inbound_token_arn
  callback_api_key_secret_arn = module.secrets.callback_api_key_arn
  database_url_secret_arn     = module.rds.database_url_secret_arn
  rds_security_group_id       = module.rds.security_group_id
  alb_arn_suffix              = module.alb.alb_arn_suffix
  target_group_arn_suffix     = module.alb.target_group_arn_suffix

  gemini_model            = var.gemini_model
  task_cpu                = 256
  task_memory             = 512
  min_capacity            = 0
  max_capacity            = 2
  llm_timeout             = 60
  callback_retry_attempts = 2
  log_retention_days      = 7
  db_pool_size            = var.db_pool_size
  db_pool_max_overflow    = var.db_pool_max_overflow
  db_pool_timeout_seconds = 10
  migrator_cpu            = 512
  migrator_memory         = 1024
  assign_public_ip        = var.assign_public_ip
}

module "monitoring" {
  source = "../../modules/monitoring"

  environment             = "dev"
  alert_email             = var.alert_email
  ecs_cluster_name        = module.ecs.cluster_name
  alb_arn_suffix          = module.alb.alb_arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_enabled             = true
  rds_instance_id         = module.rds.instance_id
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
