package com.hubara.operator.core.model

/** Plantilla aprobada de WhatsApp para reactivar una conversación con la ventana de 24 h cerrada. */
data class Template(
    val name: String,
    val body: String?,
    val variables: List<TemplateVariable>,
    val isDefault: Boolean,
    /** La plantilla lleva foto en el encabezado (todavía no se puede mandar desde la app). */
    val needsImage: Boolean,
) {
    /** El texto con las variables puestas, para mostrar antes de enviar. */
    fun preview(values: Map<String, String>): String {
        var text = body.orEmpty()
        variables.forEachIndexed { i, v ->
            text = text.replace("{{${i + 1}}}", values[v.name].orEmpty().ifEmpty { "[${v.description ?: v.name}]" })
        }
        return text
    }

    fun missing(values: Map<String, String>): List<String> = variables.filter { values[it.name].isNullOrBlank() }.map { it.name }

    /** Nombre para el operador: el catálogo trae ids técnicos. Una plantilla nueva sin entrada igual se lee. */
    val title: String
        get() = TEMPLATE_LABELS[name]
            ?: name.replace(Regex("_v\\d+$"), "").replace(Regex("_(utility|marketing)$"), "").replace('_', ' ')

    /** El título en la lista, marcando la recomendada. */
    val label: String get() = if (isDefault) "$title · recomendada" else title
}

/** Los mismos nombres que `ReactivateConversationModal.tsx` del dashboard web. */
private val TEMPLATE_LABELS = mapOf(
    "human_followup_utility_v1" to "Seguimiento del equipo (mensaje libre)",
    "order_ready_photo_utility_v1" to "Pedido listo (con foto)",
    "quote_ready_utility_v2" to "Cotización lista",
    "payment_pending_utility_v2" to "Pago pendiente",
    "order_status_utility_v2" to "Estado del pedido",
    "cart_recovery_marketing_v2" to "Carrito pendiente",
    "campaign_promo_marketing_v1" to "Campaña promocional",
    "followup_interest_marketing_v1" to "Seguimiento de interés",
)

data class TemplateVariable(val name: String, val description: String?, val maxLength: Int?)
