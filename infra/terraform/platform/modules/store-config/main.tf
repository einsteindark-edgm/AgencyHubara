# Datos comerciales PÚBLICOS de la tienda por tenant, como parámetros SSM
# `String` en /hubara/<tenant>/<VAR> — los MISMOS nombres que lee el código y
# que render-env-from-ssm.sh baja al .env. Hoy: la llave Nequi/Bre-B del pago
# anticipado (hubara_agency/src/plugins/chats/agent/sales/config/payments.py).
#
# Es un dato que el bot le escribe al cliente para que PAGUE: vive en git,
# revisado por PR, nunca en un `put-parameter` a mano. Sin valor (null) no se
# crea el parámetro y manda el default del código — en un clon de forge ese
# default es vacío: sin pago anticipado hasta que la tienda ponga su llave.

variable "tenant" { type = string }
variable "config" {
  description = "Datos comerciales del tenant (ver variables.tf → tenants.store)."
  type = object({
    payment_nequi_number = optional(string)
  })
}

locals {
  prefix = "/hubara/${var.tenant}"
  params = { for k, v in {
    PAYMENT_NEQUI_NUMBER = var.config.payment_nequi_number
  } : k => v if v != null && v != "" }
}

resource "aws_ssm_parameter" "store" {
  for_each = local.params

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} — tienda: ${each.key} (fuente de verdad: Terraform, tenants.auto.tfvars)"
  tier        = "Standard"
}

output "param_names" { value = [for p in aws_ssm_parameter.store : p.name] }
