# Mismo switch robotocore que platform/providers.tf. Real = aws_endpoint vacío;
# local = http://localhost:4566.

locals {
  use_local = var.aws_endpoint != ""
}

provider "aws" {
  region = var.region

  access_key                  = local.use_local ? "test" : null
  secret_key                  = local.use_local ? "test" : null
  skip_credentials_validation = local.use_local
  skip_metadata_api_check     = local.use_local
  skip_requesting_account_id  = local.use_local

  dynamic "endpoints" {
    for_each = local.use_local ? [1] : []
    content {
      ec2 = var.aws_endpoint
      iam = var.aws_endpoint
      sts = var.aws_endpoint
      ssm = var.aws_endpoint
    }
  }

  default_tags {
    tags = {
      Project   = "AgencyHubara"
      ManagedBy = "Terraform"
      Root      = "compute"
    }
  }
}
