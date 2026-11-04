# Política comercial PÚBLICA de la tienda por tenant, como parámetros SSM
# `String` en /hubara/<tenant>/<VAR> — los MISMOS nombres que lee el código y
# que render-env-from-ssm.sh baja al .env:
#   PAYMENT_NEQUI_NUMBER, PAYMENT_LINK_SURCHARGE_{LOCAL,OTHER}
#     → hubara_agency/src/plugins/chats/agent/sales/config/payments.py
#   SHIPPING_LOCAL_{ZONE,CITY}, SHIPPING_RATE_{LOCAL,NATIONAL}_COP, CASH_ON_DELIVERY_MIN_COP
#     → .../sales/config/shipping.py
#   STORE_SKU_PREFIX, STORE_WEB_DOMAIN → .../sales/config/store_codes.py (+ visión)
#   CATALOG_COLLECTION_HANDLES → hubara_agency/src/plugins/catalog/agent/composition.py
#
# Es lo que el bot le dice al cliente (precios, cómo pagar): vive en git,
# revisado por PR, nunca en un `put-parameter` a mano. Un campo sin valor
# (null) no crea parámetro y manda el default del código (la política de la
# tienda madre). En un clon de forge el template los escribe todos desde
# client.yaml → commerce, y la llave Nequi vacía = sin pago anticipado.

variable "tenant" { type = string }
variable "config" {
  description = "Política comercial del tenant (ver variables.tf → tenants.store)."
  type = object({
    payment_nequi_number         = optional(string)
    payment_link_surcharge_local = optional(string)
    payment_link_surcharge_other = optional(string)
    shipping_local_zone          = optional(string)
    shipping_local_city          = optional(string)
    shipping_rate_local_cop      = optional(number)
    shipping_rate_national_cop   = optional(number)
    cash_on_delivery_min_cop     = optional(number)
    sku_prefix                   = optional(string)
    web_domain                   = optional(string)
    catalog_collections          = optional(list(string))
  })
}

locals {
  prefix = "/hubara/${var.tenant}"
  values = {
    PAYMENT_NEQUI_NUMBER         = var.config.payment_nequi_number
    PAYMENT_LINK_SURCHARGE_LOCAL = var.config.payment_link_surcharge_local
    PAYMENT_LINK_SURCHARGE_OTHER = var.config.payment_link_surcharge_other
    SHIPPING_LOCAL_ZONE          = var.config.shipping_local_zone
    SHIPPING_LOCAL_CITY          = var.config.shipping_local_city
    SHIPPING_RATE_LOCAL_COP      = var.config.shipping_rate_local_cop == null ? null : tostring(var.config.shipping_rate_local_cop)
    SHIPPING_RATE_NATIONAL_COP   = var.config.shipping_rate_national_cop == null ? null : tostring(var.config.shipping_rate_national_cop)
    CASH_ON_DELIVERY_MIN_COP     = var.config.cash_on_delivery_min_cop == null ? null : tostring(var.config.cash_on_delivery_min_cop)
    STORE_SKU_PREFIX             = var.config.sku_prefix
    STORE_WEB_DOMAIN             = var.config.web_domain
    CATALOG_COLLECTION_HANDLES   = var.config.catalog_collections == null ? null : join(",", var.config.catalog_collections)
  }
  params = { for k, v in local.values : k => v if v != null && v != "" }
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
