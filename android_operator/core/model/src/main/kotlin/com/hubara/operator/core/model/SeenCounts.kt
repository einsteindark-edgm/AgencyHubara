package com.hubara.operator.core.model

/**
 * No leídos de la bandeja, como WhatsApp y como el dashboard web (#384): cuántos mensajes del
 * cliente llegaron desde la última vez que el operador abrió el chat, sin importar quién habló
 * último. El backend da el TOTAL (`inbound_count`); acá se recuerda cuántos había al abrir cada chat.
 *
 * Primera carga: lo que ya existía cuenta como visto (si no, la bandeja amanecería con contadores
 * de hace semanas). Un chat que aparece DESPUÉS de esa línea base cuenta todos sus mensajes.
 */
data class SeenCounts(val baseline: Boolean = false, val seen: Map<String, Int> = emptyMap()) {
    /** Fija la línea base con la primera bandeja que llega. Devuelve el mismo estado si nada cambió. */
    fun withBaseline(chats: List<Conversation>): SeenCounts {
        if (baseline || chats.isEmpty()) return this
        return SeenCounts(baseline = true, seen = seen + chats.associate { it.sessionId.raw to it.inboundCount })
    }

    /** El chat abierto queda visto hasta su último mensaje. Devuelve el mismo estado si ya lo estaba. */
    fun markSeen(sessionId: SessionId, inboundCount: Int): SeenCounts =
        if (seen[sessionId.raw] == inboundCount) this else copy(seen = seen + (sessionId.raw to inboundCount))

    fun unseen(chat: Conversation): Int = (chat.inboundCount - (seen[chat.sessionId.raw] ?: 0)).coerceAtLeast(0)
}
