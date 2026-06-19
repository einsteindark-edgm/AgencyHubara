# Mismo patrón que platform/backend.tf.
#   real:        terraform init -backend-config=envs/real.s3.tfbackend
#   robotocore:  terraform init -backend=false
terraform {
  backend "s3" {}
}
