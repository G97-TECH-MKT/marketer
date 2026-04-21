terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "marketer"
      Environment = "dev"
      ManagedBy   = "terraform"
      Owner       = "platform-team"
    }
  }
}
