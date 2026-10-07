# Config del laboratorio de conversaciones y de las capas del bot con
# clasificador, por tenant, como parámetros SSM `String` en /hubara/<tenant>/<VAR>
# — los MISMOS nombres que lee el código y que render-env-from-ssm.sh baja al .env
# (lo barre recursivo: no hace falta tocar el script).
#
# Por qué acá y no en `secrets`: son DECISIONES de producción (hasta dónde puede
# llegar el bot nuevo, con qué clasificador, cuánto puede gastar el laboratorio)
# y deben quedar en git, revisadas por PR. Terraform es la fuente de verdad: un
# put-parameter a mano lo REVIERTE el próximo apply (a propósito, sin ignore_changes).
#
#   SALES_PERCEPTION_MODE_CEILING  off | shadow | canary | on. Es el TECHO: el
#     control del dashboard nunca lo supera, y apagar nunca se bloquea (§4.4).
#   SALES_PERCEPTION_PROFILE       perfil activo en producción (profiles.yaml).
#   SALES_SIGNAL_INBOUND_META      on | off. La API manda los ids del mensaje como
#     4.º argumento de send_message. Encender SOLO con el worker que lo acepta
#     ya desplegado (un worker viejo falla la tarea del workflow).
#   SALES_CAPABILITIES_CEILING     off | shadow | canary | on. TECHO de las capacidades
#     del motor de decisiones (MOTOR_DECISIONES_PLAN.md): cada una va de reglas
#     (off) a sombra y a Jev (canary/on) desde el panel «Motor de decisiones»,
#     nunca por encima de esto. Bajar nunca se bloquea.
#   SALES_WORKFLOW_V2_CEILING      off | canary | on. TECHO del workflow de ventas V2
#     (canary = números de prueba y porcentaje del control; on = todos).
#   ORDER_SENTINEL_READER          off | shadow | on. El lector de Jev del Order Sentinel
#     (motor de decisiones F8): off = decide el LLM como hoy; shadow = Jev lee el
#     estado del pedido, actúa el LLM y los desacuerdos van a la cola que califica
#     Claude Code; on = actúa el veredicto de Jev cuando lo hay (si duda, el LLM).
#     Es el interruptor mismo (lote diario, sin panel): se cambia solo acá.
#   SALES_DECISIONS_BUNDLE         paquete de la tienda: la carpeta en
#     chats/shared/decisions/bundles/ (PAQUETES_DE_DECISION.md §10.1 y F5). De ahí
#     salen las capacidades (preguntas, umbrales, tablas) y el dominio de la
#     tienda (los ejemplos que el agente ve en sus herramientas); un paquete
#     que no existe falla fuerte (nunca corre la inteligencia de otra tienda).
#   REMARKETING_MAX_TOUCHES        0..5. TECHO de cuántos toques hace el bot de
#     remarketing por cada vez que el cliente deja de contestar (la escalera
#     tiene 5 peldaños: +2h/+4h/+8h/+14h/+20h). El operador elige la cantidad en
#     el dashboard (Agents → Remarketing → Frecuencia) DENTRO de este techo y
#     nunca lo supera; 5 = el comportamiento de siempre. Bajarlo aplica sobre lo
#     guardado (gana el menor). Lo leen la API y los workers de remarketing.
#   SALES_PROMPT_TURN_CONTEXT      off | team | on. Dónde van las notas de cada turno
#     de ventas (hora de Bogotá, DATOS DEL PEDIDO, nota de la percepción): off = en
#     las instrucciones (`# Retrieved Context`), como antes; on = en el mensaje del
#     turno, así las instrucciones son idénticas turno a turno y el caché de prefijo
#     de DeepSeek las cubre con las tools (45 % → 99 % cacheado, medido 2026-10-06);
#     team = solo las conversaciones de LAB_INTERNAL_NUMBERS (el piloto).
#   LAB_MAX_USD_PER_RUN / _MONTH   topes de gasto del botón "Nueva corrida" (§3.7).
#   LAB_INTERNAL_NUMBERS           teléfonos del equipo (E.164, separados por coma):
#     sus conversaciones no entran al banco (motivo `numero_interno`). Lo leen la
#     API (estimado) y el worker sales_eval (exportador). Lista vacía = el
#     placeholder de SSM (no admite valor vacío); el exportador compara solo
#     dígitos, así que el placeholder no excluye a nadie.
#
# La llave (OPENROUTER_API_KEY) es secreta: vive en el módulo `secrets`.

variable "tenant" { type = string }
variable "config" {
  description = "Config del laboratorio del tenant (ver variables.tf → tenants.lab)."
  type = object({
    perception_mode_ceiling = string
    perception_profile      = string
    signal_inbound_meta     = bool
    max_usd_per_run         = number
    max_usd_per_month       = number
    internal_numbers        = list(string)
    capabilities_ceiling    = optional(string, "off")
    workflow_v2_ceiling     = optional(string, "off")
    order_sentinel_reader   = optional(string, "off")
    decisions_bundle        = optional(string, "ventas")
    remarketing_max_touches = optional(number, 5)
    prompt_turn_context     = optional(string, "off")
  })
}

locals {
  prefix      = "/hubara/${var.tenant}"
  placeholder = "PLACEHOLDER_set_out_of_band"
  params = {
    SALES_PERCEPTION_MODE_CEILING = var.config.perception_mode_ceiling
    SALES_PERCEPTION_PROFILE      = var.config.perception_profile
    SALES_SIGNAL_INBOUND_META     = var.config.signal_inbound_meta ? "on" : "off"
    LAB_MAX_USD_PER_RUN           = tostring(var.config.max_usd_per_run)
    LAB_MAX_USD_PER_MONTH         = tostring(var.config.max_usd_per_month)
    LAB_INTERNAL_NUMBERS          = length(var.config.internal_numbers) > 0 ? join(",", var.config.internal_numbers) : local.placeholder
    SALES_CAPABILITIES_CEILING    = var.config.capabilities_ceiling
    SALES_WORKFLOW_V2_CEILING     = var.config.workflow_v2_ceiling
    ORDER_SENTINEL_READER         = var.config.order_sentinel_reader
    SALES_DECISIONS_BUNDLE        = var.config.decisions_bundle
    REMARKETING_MAX_TOUCHES       = tostring(var.config.remarketing_max_touches)
    SALES_PROMPT_TURN_CONTEXT     = var.config.prompt_turn_context
  }
}

resource "aws_ssm_parameter" "lab" {
  for_each = local.params

  name        = "${local.prefix}/${each.key}"
  type        = "String"
  value       = each.value
  description = "AgencyHubara ${var.tenant} — laboratorio de conversaciones: ${each.key} (fuente de verdad: Terraform, tenants.auto.tfvars)"
  tier        = "Standard"
}

output "param_names" { value = [for p in aws_ssm_parameter.lab : p.name] }
# Los valores (config no secreta) para los tests del plan.
output "params" { value = local.params }
