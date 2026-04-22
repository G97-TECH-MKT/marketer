data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-arm64"]
  }
}

resource "aws_kms_key" "bastion" {
  count                   = var.enabled ? 1 : 0
  description             = "CMK for marketer ${var.environment} bastion EBS"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "bastion" {
  count         = var.enabled ? 1 : 0
  name          = "alias/marketer-${var.environment}-bastion"
  target_key_id = aws_kms_key.bastion[0].key_id
}

resource "aws_security_group" "bastion" {
  count       = var.enabled ? 1 : 0
  name        = "marketer-${var.environment}-bastion"
  description = "Bastion host SG (no ingress, SSM only)"
  vpc_id      = var.vpc_id
}

resource "aws_security_group_rule" "egress_https" {
  count             = var.enabled ? 1 : 0
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.bastion[0].id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "HTTPS egress for SSM"
}

resource "aws_security_group_rule" "egress_postgres" {
  count                    = var.enabled && var.rds_security_group_id != "" ? 1 : 0
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.bastion[0].id
  source_security_group_id = var.rds_security_group_id
  description              = "Postgres to RDS"
}

resource "aws_iam_role" "bastion" {
  count                = var.enabled ? 1 : 0
  name                 = "marketer-${var.environment}-bastion"
  path                 = "/marketer/"
  permissions_boundary = var.permission_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "ec2.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_managed" {
  count      = var.enabled ? 1 : 0
  role       = aws_iam_role.bastion[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "read_db_secret" {
  count = var.enabled ? 1 : 0
  name  = "marketer-db-secret-read"
  role  = aws_iam_role.bastion[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.database_url_secret_arn]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "bastion" {
  count = var.enabled ? 1 : 0
  name  = "marketer-${var.environment}-bastion"
  path  = "/marketer/"
  role  = aws_iam_role.bastion[0].name
}

resource "aws_instance" "bastion" {
  count                  = var.enabled ? 1 : 0
  ami                    = data.aws_ami.al2023_arm64.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bastion[0].id]
  iam_instance_profile   = aws_iam_instance_profile.bastion[0].name

  associate_public_ip_address = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
    kms_key_id  = aws_kms_key.bastion[0].arn
  }

  user_data = file("${path.module}/user_data.sh")
}

resource "aws_iam_role" "scheduler" {
  count                = var.enabled && var.auto_stop_enabled ? 1 : 0
  name                 = "marketer-${var.environment}-bastion-scheduler"
  path                 = "/marketer/"
  permissions_boundary = var.permission_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "scheduler.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.enabled && var.auto_stop_enabled ? 1 : 0
  name  = "marketer-bastion-scheduler-stop"
  role  = aws_iam_role.scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:StopInstances"]
        Resource = [aws_instance.bastion[0].arn]
      }
    ]
  })
}

resource "aws_scheduler_schedule" "auto_stop" {
  count                        = var.enabled && var.auto_stop_enabled ? 1 : 0
  name                         = "marketer-${var.environment}-bastion-auto-stop"
  schedule_expression          = "cron(0 22 * * ? *)"
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.scheduler[0].arn
    input = jsonencode({
      InstanceIds = [aws_instance.bastion[0].id]
    })
  }
}
