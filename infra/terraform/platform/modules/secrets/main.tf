# Secretos de un tenant: parámetros SSM SecureString en /hubara/<tenant>/<KEY>.
# Coincide con INFRASTRUCTURE.md §3.7.
#
# Terraform crea la CLAVE con un placeholder; el VALOR real se setea fuera de banda
# (`aws ssm put-parameter --overwrite`) y NO entra al state ni a git. El
# `ignore_changes = [value]` evita que un `apply` posterior pise el secreto real.
#
# El instance profile de ../../compute leerá este prefijo (ssm:GetParametersByPath).

variable "tenant" { type = string }
variable "secret_keys" { type = list(string) }

locals {
  prefix = "/hubara/${var.tenant}"
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(var.secret_keys)

  name        = "${local.prefix}/${each.value}"
  type        = "SecureString"
  value       = "PLACEHOLDER_set_out_of_band"
  description = "AgencyHubara ${var.tenant} — ${each.value} (valor real se setea fuera de Terraform)"
  tier        = "Standard"

  lifecycle {
    ignore_changes = [value]
  }
}

output "ssm_prefix" { value = local.prefix }
output "parameter_arns" { value = [for p in aws_ssm_parameter.secret : p.arn] }
