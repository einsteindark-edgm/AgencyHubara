# Config de timing de los schedulers (PR #69) como parámetros SSM, por tenant.
# Son la ÚNICA fuente para cambiar CUÁNDO corren los jobs periódicos
# (orders-reconcile, sales-eval, golden-eval, remarketing-watchdog) SIN redeploy.
#
# Por qué SSM y no baked-in: el código lee `os.environ` y converge el Temporal
# Schedule en cada boot del worker. Cambiar un cron = `put-parameter --overwrite`
# + restart del worker afectado (segundos). Ver hubara_agency/docs/scheduler-config-aws.md
# (Ruta A) y INFRASTRUCTURE.md §3.7.
#
# Tipo `String` (NO SecureString): no son secretos (crons/intervalos/flags) y así
# se ven/editan fácil en la consola. Path: /hubara/<tenant>/scheduler/<VAR>, que el
# render-env-from-ssm.sh ya baja recursivo junto con los secretos del tenant.
#
# `ignore_changes = [value]`: Terraform crea la clave con el default; un cambio
# operativo (`put-parameter --overwrite`) NO lo revierte el próximo `apply`.

variable "tenant" { type = string }
variable "config" {
  description = "Mapa VAR → valor default de cada knob de scheduler."
  type        = map(string)
}

locals {
  prefix = "/hubara/${var.tenant}/scheduler"
}

resource "aws_ssm_parameter" "knob" {
  for_each = var.config

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} scheduler — ${each.key} (cambiable sin redeploy)"
  tier        = "Standard"

  lifecycle {
    ignore_changes = [value]
  }
}

output "prefix" { value = local.prefix }
output "param_names" { value = [for p in aws_ssm_parameter.knob : p.name] }
