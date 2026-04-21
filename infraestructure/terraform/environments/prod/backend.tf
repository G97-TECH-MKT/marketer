terraform {
  backend "s3" {
    bucket         = "marketer-tf-plinng"
    key            = "marketer/prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "marketer-terraform-locks"
  }
}
