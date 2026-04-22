data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_kms_key" "rds" {
  description             = "CMK for marketer ${var.environment} RDS"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "rds" {
  name          = "alias/marketer-${var.environment}-rds"
  target_key_id = aws_kms_key.rds.key_id
}

resource "aws_db_subnet_group" "marketer" {
  name       = "marketer-${var.environment}"
  subnet_ids = var.db_subnet_ids
}

resource "aws_db_parameter_group" "marketer" {
  name   = "marketer-${var.environment}-pg17"
  family = "postgres17"

  parameter {
    name         = "log_min_duration_statement"
    value        = "500"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "log_connections"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "log_disconnections"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements,pgaudit"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "pgaudit.log"
    value        = "ddl,role"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "idle_in_transaction_session_timeout"
    value        = "60000"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "max_connections"
    value        = tostring(var.max_connections)
    apply_method = "pending-reboot"
  }
}

resource "aws_security_group" "rds" {
  name        = "marketer-${var.environment}-rds"
  description = "RDS security group"
  vpc_id      = var.vpc_id
}

resource "aws_security_group_rule" "rds_from_ecs" {
  count                    = var.ecs_security_group_id != "" ? 1 : 0
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = var.ecs_security_group_id
  description              = "Postgres from ECS"
}

resource "aws_security_group_rule" "rds_from_bastion" {
  count                    = var.bastion_security_group_id != "" ? 1 : 0
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = var.bastion_security_group_id
  description              = "Postgres from bastion"
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "marketer/${var.environment}/database-url"
  description             = "Database URL and credentials for marketer ${var.environment}"
  recovery_window_in_days = 7
  kms_key_id              = aws_kms_key.rds.arn
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = jsonencode({
    username    = var.db_username
    password    = random_password.db_password.result
    host        = aws_db_instance.marketer.address
    port        = 5432
    dbname      = var.db_name
    url         = "postgresql+asyncpg://${var.db_username}:${random_password.db_password.result}@${aws_db_instance.marketer.address}:5432/${var.db_name}?ssl=require"
    alembic_url = "postgresql+psycopg://${var.db_username}:${random_password.db_password.result}@${aws_db_instance.marketer.address}:5432/${var.db_name}?sslmode=require"
  })
}

resource "aws_iam_role" "rds_monitoring" {
  name                 = "marketer-${var.environment}-rds-monitoring"
  path                 = "/marketer/"
  permissions_boundary = var.permission_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "monitoring.rds.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring_managed" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_cloudwatch_log_group" "postgresql" {
  name              = "/aws/rds/instance/marketer-${var.environment}/postgresql"
  retention_in_days = var.log_retention_days
}

resource "aws_db_instance" "marketer" {
  identifier        = "marketer-${var.environment}"
  engine            = "postgres"
  engine_version    = "17.9"
  instance_class    = var.instance_class
  username          = var.db_username
  password          = random_password.db_password.result
  db_name           = var.db_name
  port              = 5432
  multi_az          = var.multi_az
  storage_type      = "gp3"
  allocated_storage = var.allocated_storage

  max_allocated_storage               = var.max_allocated_storage
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.rds.arn
  iam_database_authentication_enabled = true
  ca_cert_identifier                  = "rds-ca-rsa2048-g1"

  db_subnet_group_name   = aws_db_subnet_group.marketer.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.marketer.name

  backup_retention_period  = var.backup_retention_days
  backup_window            = "03:00-04:00"
  maintenance_window       = "sun:04:30-sun:05:30"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false

  auto_minor_version_upgrade  = true
  apply_immediately           = false
  allow_major_version_upgrade = false
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = false
  final_snapshot_identifier   = "marketer-${var.environment}-final"

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  performance_insights_kms_key_id       = aws_kms_key.rds.arn

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  depends_on = [aws_cloudwatch_log_group.postgresql]
}
