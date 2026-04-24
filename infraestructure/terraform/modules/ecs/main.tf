data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_security_group" "ecs" {
  name        = "marketer-${var.environment}-ecs"
  description = "ECS Fargate tasks for Marketer - inbound from ALB only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTP from ALB"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    description = "HTTPS egress for Gemini API and callbacks"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description     = "Postgres egress to RDS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.rds_security_group_id]
  }
}

resource "aws_cloudwatch_log_group" "marketer" {
  name              = "/ecs/marketer-${var.environment}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "migrator" {
  name              = "/ecs/marketer-${var.environment}-migrator"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "marketer" {
  name = "marketer-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "marketer" {
  cluster_name       = aws_ecs_cluster.marketer.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_ecs_task_definition" "marketer" {
  family                   = "marketer-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "marketer"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "GEMINI_MODEL", value = var.gemini_model },
        { name = "LLM_TIMEOUT_SECONDS", value = tostring(var.llm_timeout) },
        { name = "LLM_FANOUT_ENABLED", value = tostring(var.llm_fanout_enabled) },
        { name = "LLM_FANOUT_CONCURRENCY", value = tostring(var.llm_fanout_concurrency) },
        { name = "CALLBACK_RETRY_ATTEMPTS", value = tostring(var.callback_retry_attempts) },
        { name = "CALLBACK_HTTP_TIMEOUT_SECONDS", value = "30" },
        { name = "EXTRAS_LIST_TRUNCATION", value = "10" },
        { name = "DB_POOL_SIZE", value = tostring(var.db_pool_size) },
        { name = "DB_POOL_MAX_OVERFLOW", value = tostring(var.db_pool_max_overflow) },
        { name = "DB_POOL_TIMEOUT_SECONDS", value = tostring(var.db_pool_timeout_seconds) },
      ]
      secrets = [
        { name = "GEMINI_API_KEY", valueFrom = var.gemini_api_key_secret_arn },
        { name = "INBOUND_TOKEN", valueFrom = var.inbound_token_secret_arn },
        { name = "ORCH_CALLBACK_API_KEY", valueFrom = var.callback_api_key_secret_arn },
        { name = "AGENTIC_DISPATCHER_URL", valueFrom = var.agentic_dispatcher_url_secret_arn },
        { name = "DATABASE_URL", valueFrom = "${var.database_url_secret_arn}:url::" },
      ]
      healthCheck = {
        command     = ["CMD", "curl", "-f", "http://localhost:8080/ready"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.marketer.name
          "awslogs-region"        = data.aws_region.current.id
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

resource "aws_iam_role" "migrator_task" {
  name                 = "marketer-${var.environment}-migrator-task"
  path                 = "/marketer/"
  permissions_boundary = var.permission_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "migrator_task" {
  name = "marketer-migrator-task-policy"
  role = aws_iam_role.migrator_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:log-group:${aws_cloudwatch_log_group.migrator.name}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.database_url_secret_arn]
      }
    ]
  })
}

resource "aws_ecs_task_definition" "migrator" {
  family                   = "marketer-${var.environment}-migrator"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.migrator_cpu)
  memory                   = tostring(var.migrator_memory)
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = aws_iam_role.migrator_task.arn

  container_definitions = jsonencode([
    {
      name      = "migrator"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["sh", "-c", "alembic -c /app/alembic.ini upgrade head"]
      secrets = [
        { name = "DATABASE_URL", valueFrom = "${var.database_url_secret_arn}:alembic_url::" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migrator.name
          "awslogs-region"        = data.aws_region.current.id
          "awslogs-stream-prefix" = "migrator"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "marketer" {
  name                              = "marketer"
  cluster                           = aws_ecs_cluster.marketer.id
  task_definition                   = aws_ecs_task_definition.marketer.arn
  desired_count                     = var.min_capacity
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 60
  force_new_deployment              = true

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "marketer"
    container_port   = 8080
  }

  deployment_controller {
    type = "ECS"
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "marketer" {
  max_capacity       = var.max_capacity
  min_capacity       = var.min_capacity
  resource_id        = "service/${aws_ecs_cluster.marketer.name}/marketer"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
  depends_on         = [aws_ecs_service.marketer]
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "marketer-${var.environment}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = aws_appautoscaling_target.marketer.scalable_dimension
  service_namespace  = aws_appautoscaling_target.marketer.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 70.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "memory" {
  name               = "marketer-${var.environment}-memory"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = aws_appautoscaling_target.marketer.scalable_dimension
  service_namespace  = aws_appautoscaling_target.marketer.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 75.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "requests" {
  name               = "marketer-${var.environment}-requests"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = aws_appautoscaling_target.marketer.scalable_dimension
  service_namespace  = aws_appautoscaling_target.marketer.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 60.0
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${var.alb_arn_suffix}/${var.target_group_arn_suffix}"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "cpu_burst" {
  name               = "marketer-${var.environment}-cpu-burst"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.marketer.resource_id
  scalable_dimension = aws_appautoscaling_target.marketer.scalable_dimension
  service_namespace  = aws_appautoscaling_target.marketer.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Average"

    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 15
      scaling_adjustment          = 1
    }

    step_adjustment {
      metric_interval_lower_bound = 15
      metric_interval_upper_bound = 25
      scaling_adjustment          = 2
    }

    step_adjustment {
      metric_interval_lower_bound = 25
      scaling_adjustment          = 3
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_burst_high" {
  alarm_name          = "marketer-${var.environment}-cpu-burst-high"
  alarm_description   = "Burst scale-out for CPU spikes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 70
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.cpu_burst.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.marketer.name
    ServiceName = aws_ecs_service.marketer.name
  }
}
