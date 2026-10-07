package com.hubara.operator.core.model

/**
 * Los datos que el cliente llenó en el formulario de envío de WhatsApp. Llegan al historial como
 * `[datos de envío recibidos] receiver_name=…; city=…` (el mismo formato que proyecta
 * `hubara_agency/src/plugins/chats/shared/chat_events.py` para la tarjeta del dashboard).
 */
data class ShippingForm(
    val receiverName: String?,
    val phone: String?,
    val city: String?,
    val neighborhood: String?,
    val address: String?,
    val paymentMethod: String?,
) {
    companion object {
        private const val MARKER = "[datos de envío recibidos]"
        private val SPLIT = Regex("\\s*;\\s*(?=\\w+=)")

        /** null si el texto no es el formulario. Sin pares («(sin datos)») = formulario vacío. */
        fun parse(text: String?): ShippingForm? {
            if (text == null) return null
            val start = text.indexOf(MARKER).takeIf { it >= 0 } ?: return null
            val raw = text.substring(start + MARKER.length).trim()
            val values = if ("=" !in raw) emptyMap() else raw.split(SPLIT).mapNotNull { part ->
                val key = part.substringBefore("=", "").trim()
                val value = part.substringAfter("=", "").trim()
                if (key.isEmpty() || value.isEmpty()) null else key to value
            }.toMap()
            return ShippingForm(
                receiverName = values["receiver_name"],
                phone = values["phone"],
                city = values["city"],
                neighborhood = values["neighborhood"],
                address = values["address"],
                paymentMethod = values["payment_method"],
            )
        }
    }
}
