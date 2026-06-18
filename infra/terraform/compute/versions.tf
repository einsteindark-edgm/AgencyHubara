# Root `compute/` — EC2: la caja de app por tenant (FastAPI + LiteLLM + 6 workers
# + Caddy) y la caja de observabilidad compartida (SigNoz). State separado del
# `platform/` → menor blast radius (podés recrear compute sin tocar buckets/pools).
#
# Todo es AWS, así que robotocore TAMBIÉN puede testear el plan/apply de EC2
# (run_instances, SGs, EIP, instance profiles). El cloud-init no ejecuta en local.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}
