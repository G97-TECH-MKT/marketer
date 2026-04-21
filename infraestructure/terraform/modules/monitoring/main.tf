# ─── SNS Topic ────────────────────────────────────────────────────────────────

data "aws_region" "current" {}

resource "aws_sns_topic" "alerts" {
  name = "marketer-${var.environment}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ─── Alarms ───────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  alarm_name          = "marketer-${var.environment}-unhealthy-tasks"
  alarm_description   = "Marketer: unhealthy ECS tasks detected"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    TargetGroup  = var.target_group_arn_suffix
    LoadBalancer = var.alb_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "high_5xx" {
  alarm_name          = "marketer-${var.environment}-5xx-errors"
  alarm_description   = "Marketer: elevated HTTP 5xx error rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "marketer-${var.environment}-high-cpu"
  alarm_description   = "Marketer: ECS CPU utilization above 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = "marketer"
  }
}

resource "aws_cloudwatch_metric_alarm" "high_memory" {
  alarm_name          = "marketer-${var.environment}-high-memory"
  alarm_description   = "Marketer: ECS memory utilization above 85%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 85
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = "marketer"
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_latency" {
  alarm_name          = "marketer-${var.environment}-high-latency"
  alarm_description   = "Marketer: ALB target response time p95 > 25s"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  extended_statistic  = "p95"
  threshold           = 25
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

# ─── CloudWatch Dashboard ─────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "marketer" {
  dashboard_name = "marketer-${var.environment}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type       = "metric"
        x          = 0
        y          = 0
        width      = 12
        height     = 6
        properties = {
          title   = "Request Count (per minute)"
          metrics = [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix]]
          region  = data.aws_region.current.id
          period  = 60
          stat    = "Sum"
          view    = "timeSeries"
        }
      },
      {
        type       = "metric"
        x          = 12
        y          = 0
        width      = 12
        height     = 6
        properties = {
          title   = "HTTP 5xx Errors"
          metrics = [["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix]]
          region  = data.aws_region.current.id
          period  = 60
          stat    = "Sum"
          view    = "timeSeries"
        }
      },
      {
        type       = "metric"
        x          = 0
        y          = 6
        width      = 12
        height     = 6
        properties = {
          title = "Target Response Time (ms)"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50", label = "p50" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p95", label = "p95" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p99", label = "p99" }],
          ]
          region = data.aws_region.current.id
          period = 60
          view   = "timeSeries"
        }
      },
      {
        type       = "metric"
        x          = 12
        y          = 6
        width      = 12
        height     = 6
        properties = {
          title = "ECS CPU & Memory Utilization"
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", "marketer", { label = "CPU %" }],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", "marketer", { label = "Memory %" }],
          ]
          region = data.aws_region.current.id
          period = 60
          stat   = "Average"
          view   = "timeSeries"
        }
      },
      {
        type       = "metric"
        x          = 0
        y          = 12
        width      = 12
        height     = 6
        properties = {
          title   = "Running Task Count"
          metrics = [["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.ecs_cluster_name, "ServiceName", "marketer"]]
          region  = data.aws_region.current.id
          period  = 60
          stat    = "Average"
          view    = "timeSeries"
        }
      },
      {
        type       = "metric"
        x          = 12
        y          = 12
        width      = 12
        height     = 6
        properties = {
          title = "ALB Healthy / Unhealthy Hosts"
          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", "TargetGroup", var.target_group_arn_suffix, "LoadBalancer", var.alb_arn_suffix, { label = "Healthy" }],
            ["AWS/ApplicationELB", "UnHealthyHostCount", "TargetGroup", var.target_group_arn_suffix, "LoadBalancer", var.alb_arn_suffix, { label = "Unhealthy" }],
          ]
          region = data.aws_region.current.id
          period = 60
          stat   = "Average"
          view   = "timeSeries"
        }
      }
    ]
  })
}
