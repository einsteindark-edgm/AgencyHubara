# Reseñas tras la entrega (plugin `reviews`, decisión del operador 2026-10-13):
# la invitación a calificar un pedido entregado, el cupón de agradecimiento y
# el botón a Google. Parámetros SSM `String` en /hubara/<tenant>/<VAR> — los
# MISMOS nombres que lee `src/plugins/reviews/agent/settings.py` y que
# render-env-from-ssm.sh baja al .env. Git es la fuente de verdad (sin
# ignore_changes: un put-parameter a mano lo revierte el próximo apply).
#
#   REVIEW_REQUESTS_MODE   off | test | on. Nace en off. `test` = solo los
#     números de `test_sessions`; `on` = todos los pedidos entregados.
#   REVIEW_TEST_SESSIONS   sesiones del vault (wa_57…) que reciben la invitación
#     en modo test. Lista vacía = el placeholder de SSM (no admite vacío).
#   REVIEW_REQUEST_*_MINUTES  tiempos: mínimo tras la entrega (120), margen antes
#     de que cierre la ventana (60), desde cuándo va la plantilla de marketing
#     si la ventana está cerrada (1200 = 20 h), cuándo se deja (2880) y cada
#     cuánto se mira si el cliente abrió la ventana (30).
#   REVIEW_COUPON_CODE / _PHOTO_CODE / _DAYS  el cupón personal de 5 % y de 10 %
#     (con foto y permiso para publicarla) y su vigencia en días. Los cupones
#     se crean en la central (Marketing → Cupones) con «solo quien lo recibió».
#   GOOGLE_REVIEW_URL      destino del botón «Opinar en Google» (directo a la
#     ficha de Google: no depende de que la web esté publicada).

variable "tenant" { type = string }
variable "config" {
  description = "Config de reseñas del tenant (ver variables.tf → tenants.reviews)."
  type = object({
    mode                   = string
    test_sessions          = list(string)
    min_delay_minutes      = number
    window_margin_minutes  = number
    template_after_minutes = number
    give_up_minutes        = number
    poll_minutes           = number
    coupon_code            = string
    coupon_photo_code      = string
    coupon_days            = number
    google_review_url      = string
  })
}

locals {
  prefix      = "/hubara/${var.tenant}"
  placeholder = "PLACEHOLDER_set_out_of_band"
  params = {
    REVIEW_REQUESTS_MODE                  = var.config.mode
    REVIEW_TEST_SESSIONS                  = length(var.config.test_sessions) > 0 ? join(",", var.config.test_sessions) : local.placeholder
    REVIEW_REQUEST_MIN_DELAY_MINUTES      = tostring(var.config.min_delay_minutes)
    REVIEW_REQUEST_WINDOW_MARGIN_MINUTES  = tostring(var.config.window_margin_minutes)
    REVIEW_REQUEST_TEMPLATE_AFTER_MINUTES = tostring(var.config.template_after_minutes)
    REVIEW_REQUEST_GIVE_UP_MINUTES        = tostring(var.config.give_up_minutes)
    REVIEW_REQUEST_POLL_MINUTES           = tostring(var.config.poll_minutes)
    REVIEW_COUPON_CODE                    = var.config.coupon_code
    REVIEW_COUPON_PHOTO_CODE              = var.config.coupon_photo_code
    REVIEW_COUPON_DAYS                    = tostring(var.config.coupon_days)
    GOOGLE_REVIEW_URL                     = var.config.google_review_url
  }
}

resource "aws_ssm_parameter" "reviews" {
  for_each = local.params

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} — reseñas tras la entrega: ${each.key} (fuente de verdad: Terraform, tenants.auto.tfvars)"
  tier        = "Standard"
}

output "param_names" { value = [for p in aws_ssm_parameter.reviews : p.name] }
output "params" { value = local.params }
