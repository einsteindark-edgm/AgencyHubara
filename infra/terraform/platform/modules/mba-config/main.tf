# Config de Meta Business Agent (MBA) por tenant, como parámetros SSM `String`
# en /hubara/<tenant>/<VAR> — los MISMOS nombres que lee el código
# (hubara_agency/src/platform/config.py) y que render-env-from-ssm.sh baja al .env.
#
# Por qué acá y no en `secrets` (placeholder + ignore_changes): estamos en
# producción y MBA solo puede responderle a una LISTA CERRADA. Quién está en esa
# lista y si el oído `standby` está encendido son DECISIONES que deben quedar en
# git, revisadas por PR y replicables tenant a tenant — no un `put-parameter` a
# mano que nadie recuerda. Terraform es la fuente de verdad: un cambio operativo
# fuera de banda lo REVIERTE el próximo `apply` (a propósito, sin ignore_changes).
#
# Semántica que espera el código (fail-closed en todos los casos):
#   MBA_STANDBY_ENABLED / MBA_EPISODE_BOUNDARY_EVENT / MBA_ALLOW_EVERYONE → "1" | "0"
#     (parse_flag: solo 1/true/yes/on es True).
#   MBA_CUSTOMER_ALLOWLIST → E.164 separados por coma; lista vacía = el placeholder
#     de SSM (parse_customer_allowlist lo lee como NADIE; SSM no admite valor vacío).
#
# Los SECRETOS de MBA (META_MBA_TOKEN, WHATSAPP_APP_ID, HUBARA_MBA_API_KEY) siguen
# en el módulo `secrets`: se setean fuera de banda y nunca entran a git.

variable "tenant" { type = string }
variable "config" {
  description = "Config MBA del tenant (ver variables.tf → tenants.mba)."
  type = object({
    standby_enabled        = bool
    customer_allowlist     = list(string)
    episode_boundary_event = bool
    allow_everyone         = bool
  })
}

locals {
  prefix      = "/hubara/${var.tenant}"
  placeholder = "PLACEHOLDER_set_out_of_band"
  flag = { for k, v in {
    MBA_STANDBY_ENABLED        = var.config.standby_enabled
    MBA_EPISODE_BOUNDARY_EVENT = var.config.episode_boundary_event
    MBA_ALLOW_EVERYONE         = var.config.allow_everyone
  } : k => (v ? "1" : "0") }
  allowlist = length(var.config.customer_allowlist) > 0 ? join(",", var.config.customer_allowlist) : local.placeholder
  params    = merge(local.flag, { MBA_CUSTOMER_ALLOWLIST = local.allowlist })
}

resource "aws_ssm_parameter" "mba" {
  for_each = local.params

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} — Meta Business Agent: ${each.key} (fuente de verdad: Terraform, tenants.auto.tfvars)"
  tier        = "Standard"
}

output "param_names" { value = [for p in aws_ssm_parameter.mba : p.name] }
