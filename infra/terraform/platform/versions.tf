# Root `platform/` — capa AWS *managed* (S3+CloudFront, Cognito, SSM, IAM/OIDC).
# Es la capa estable (cambia poco) y la que se testea END-TO-END en robotocore
# (todos estos servicios los emula el :4566). El compute EC2 vive en ../compute.
#
# State: S3 + DynamoDB lock en real (ver backend.tf); local en robotocore.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 5.100 es la versión con la que se probó `managed_login_version` del dominio
      # de Cognito (modules/auth); las primeras 5.x no traen ese atributo.
      version = "~> 5.100"
    }
  }
}
