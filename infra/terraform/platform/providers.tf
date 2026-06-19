# Provider AWS con SWITCH robotocore.
#
# Real:        dejar `aws_endpoint = ""` (default). Usa credenciales reales
#              (perfil/role/OIDC) y los endpoints de AWS.
# robotocore:  pasar `-var aws_endpoint=http://localhost:4566` (o el local.tfvars
#              de ../../robotocore). Activa creds dummy + path-style S3 + apunta
#              TODOS los services al :4566. Cero llamadas a AWS real.
#
# El mismo código aplica en ambos — solo cambia esta variable. Eso es lo que nos
# deja "probar para no equivocarnos en la real".

locals {
  use_local = var.aws_endpoint != ""
}

provider "aws" {
  region = var.region

  # --- Overrides solo-local (robotocore / LocalStack-compatible) ---
  access_key                  = local.use_local ? "test" : null
  secret_key                  = local.use_local ? "test" : null
  skip_credentials_validation = local.use_local
  skip_metadata_api_check     = local.use_local
  skip_requesting_account_id  = local.use_local
  s3_use_path_style           = local.use_local

  # CloudFront + ACM viven en us-east-1 sí o sí; en local da igual (todo es :4566).
  dynamic "endpoints" {
    for_each = local.use_local ? [1] : []
    content {
      s3         = var.aws_endpoint
      cloudfront = var.aws_endpoint
      cognitoidp = var.aws_endpoint
      ssm        = var.aws_endpoint
      iam        = var.aws_endpoint
      sts        = var.aws_endpoint
      acm        = var.aws_endpoint
    }
  }

  default_tags {
    tags = {
      Project   = "AgencyHubara"
      ManagedBy = "Terraform"
      Root      = "platform"
    }
  }
}
