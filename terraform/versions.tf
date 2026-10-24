terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }

  # State is local (and git-ignored) until a real deployment. Then switch to an
  # encrypted S3 backend with native locking:
  #
  # backend "s3" {
  #   bucket       = "<your-unique-state-bucket>"
  #   key          = "serverless-data-pipeline/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}
