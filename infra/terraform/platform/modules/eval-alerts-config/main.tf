# Alertas del scorecard de ventas (worker sales_eval, Ciclo Colossus F4,
# 2026-11-03): el repo de GitHub donde se abren los issues [ALR] con la
# etiqueta origen:alerta (episodio en FALLA y bandas por regla del barrido
# diario). Parámetro SSM `String` en /hubara/<tenant>/<VAR> — el MISMO nombre
# que lee `scorecard/alerts.py` y que render-env-from-ssm.sh baja al .env.
# Git es la fuente de verdad (sin ignore_changes: un put-parameter a mano lo
# revierte el próximo apply).
#
#   SCORECARD_ALERTS_REPO  owner/repo. Vacío en tfvars = el placeholder de SSM
#     (no admite vacío), que el código trata como «sin alertas».
#
# El token NO va acá: es el secreto SCORECARD_ALERTS_GITHUB_TOKEN de
# `secret_keys` (placeholder por Terraform, valor fuera de banda).

variable "tenant" { type = string }
variable "config" {
  description = "Alertas del scorecard del tenant (ver variables.tf → tenants.eval_alerts)."
  type = object({
    repo = string
  })
}

locals {
  prefix      = "/hubara/${var.tenant}"
  placeholder = "PLACEHOLDER_set_out_of_band"
  params = {
    SCORECARD_ALERTS_REPO = var.config.repo != "" ? var.config.repo : local.placeholder
  }
}

resource "aws_ssm_parameter" "eval_alerts" {
  for_each = local.params

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} — alertas del scorecard: ${each.key} (fuente de verdad: Terraform, tenants.auto.tfvars)"
  tier        = "Standard"
}

output "param_names" { value = [for p in aws_ssm_parameter.eval_alerts : p.name] }
output "params" { value = local.params }
