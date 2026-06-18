# State remoto en S3 + lock en DynamoDB (config PARCIAL — se completa en `init`).
#
# Real:
#   terraform init -backend-config=envs/real.s3.tfbackend
#   (el bucket + tabla DynamoDB se bootstrapean UNA vez; ver ../README.md §Bootstrap)
#
# robotocore (local):
#   terraform init -backend=false
#   → usa state LOCAL efímero (no necesitamos lock ni durabilidad para un test
#     contra el :4566). Es lo que hace ../../robotocore/test-local.sh.
#
# Dejar el bloque VACÍO (partial) es deliberado: así el mismo código sirve real
# y local sin editar nada — solo cambia el comando de `init`.
terraform {
  backend "s3" {}
}
