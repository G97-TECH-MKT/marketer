resource "aws_ecr_repository" "marketer" {
  count = var.create_repository ? 1 : 0

  name                 = "marketer"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "marketer" {
  count      = var.create_repository ? 1 : 0
  repository = aws_ecr_repository.marketer[0].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 tagged images (sha-*)"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}

data "aws_ecr_repository" "marketer" {
  count = var.create_repository ? 0 : 1
  name  = "marketer"
}

locals {
  repository_url = var.create_repository ? aws_ecr_repository.marketer[0].repository_url : data.aws_ecr_repository.marketer[0].repository_url
  repository_arn = var.create_repository ? aws_ecr_repository.marketer[0].arn : data.aws_ecr_repository.marketer[0].arn
  registry_id    = var.create_repository ? aws_ecr_repository.marketer[0].registry_id : data.aws_ecr_repository.marketer[0].registry_id
}
