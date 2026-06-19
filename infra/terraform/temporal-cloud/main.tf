# OPCIONAL — gestiona los namespaces de Temporal Cloud (hubara, vincenzo) por
# código. Coincide con INFRASTRUCTURE.md §5-§6 (1 cuenta, N namespaces, API key).
#
# NO se testea en robotocore (Temporal Cloud no es AWS) y necesita una cuenta TC
# + API key. Si preferís crear los namespaces a mano en la consola, ignorá este
# root entero — el resto de la infra no depende de él.
#
# Uso:
#   export TEMPORAL_CLOUD_API_KEY=...           # API key de TC (no la del namespace)
#   terraform init && terraform apply
#
# ⚠️ El schema del provider temporalcloud evoluciona — verificá los argumentos
#    contra la doc de tu versión antes de aplicar.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    temporalcloud = {
      source  = "temporalio/temporalcloud"
      version = "~> 0.8"
    }
  }
}

provider "temporalcloud" {
  # Toma TEMPORAL_CLOUD_API_KEY del entorno si no se pasa explícito.
  api_key = var.temporal_cloud_api_key
}

variable "temporal_cloud_api_key" {
  type      = string
  default   = null
  sensitive = true
}

variable "region" {
  description = "Región de Temporal Cloud (ej. aws-us-east-1)."
  type        = string
  default     = "aws-us-east-1"
}

variable "namespaces" {
  type    = list(string)
  default = ["hubara", "vincenzo"]
}

resource "temporalcloud_namespace" "ns" {
  for_each = toset(var.namespaces)

  name           = each.value
  regions        = [var.region]
  retention_days = 30

  # API key auth a nivel namespace (sin mTLS). Si tu versión del provider exige
  # `accepted_client_ca` (mTLS), o lo agregás, o migrás a api-key auth en la consola.
  api_key_auth = true
}

output "namespaces" {
  description = "namespace.<account_id> de cada tenant (para TEMPORAL_NAMESPACE en SSM)."
  value       = { for k, ns in temporalcloud_namespace.ns : k => ns.id }
}
