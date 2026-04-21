# ─── Security Group ───────────────────────────────────────────────────────────
# Broad egress to avoid circular dependency with ECS SG.
# The ECS SG restricts ingress to this SG only.

resource "aws_security_group" "alb" {
  name        = "marketer-${var.environment}-alb"
  description = "ALB for Marketer - inbound HTTP/HTTPS, egress to VPC"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from allowed CIDRs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  ingress {
    description = "HTTP redirect from allowed CIDRs"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  egress {
    description = "Egress to ECS tasks on port 8080"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

# ─── Application Load Balancer ────────────────────────────────────────────────

resource "aws_lb" "marketer" {
  name               = "marketer-${var.environment}"
  internal           = var.internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = var.environment == "prod"

  idle_timeout = 60
}

# ─── Target Group ─────────────────────────────────────────────────────────────

resource "aws_lb_target_group" "marketer" {
  name        = "marketer-${var.environment}"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = "/ready"
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  # Fast deregistration for rolling deploys
  deregistration_delay = 30
}

# ─── Listeners ────────────────────────────────────────────────────────────────

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.marketer.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.marketer.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.marketer.arn
  }
}
