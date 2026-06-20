# Secretos de GraphAgents en SSM /graphagents/<KEY> (SecureString). Subsistema
# separado → prefijo propio (no /hubara/<tenant>/). La caja GraphAgents (en
# ../../compute) los lee con su instance profile (`/graphagents/*`).
#
# Igual que el módulo `secrets`: Terraform crea la clave con placeholder; el valor
# real se setea fuera de banda (no entra al state ni a git). `ignore_changes`.

variable "secret_keys" { type = list(string) }

locals {
  prefix = "/graphagents"
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(var.secret_keys)

  name        = "${local.prefix}/${each.value}"
  type        = "SecureString"
  value       = "PLACEHOLDER_set_out_of_band"
  description = "AgencyHubara GraphAgents — ${each.value} (valor real fuera de Terraform)"
  tier        = "Standard"

  lifecycle {
    ignore_changes = [value]
  }
}

output "ssm_prefix" { value = local.prefix }
output "parameter_arns" { value = [for p in aws_ssm_parameter.secret : p.arn] }
