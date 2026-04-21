terraform {
  backend "s3" {
    bucket         = "REPLACE_WITH_YOUR_STATE_BUCKET"
    key            = "marketer/dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "marketer-terraform-locks"
  }
}
