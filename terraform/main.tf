provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "serverless-data-pipeline-security-automation"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "orders-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
}

# Both Lambda functions ship the same zip of src/ (pipeline + compliance).
# They need no third-party packages: the Lambda Python runtime includes boto3.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/lambda.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}
