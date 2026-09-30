package com.hubara.operator.core.model

import kotlinx.serialization.Serializable

/**
 * Id de una sesión del vault. Solo se construye validado: un id mal formado (de un deep link, de un
 * push) nunca llega a la pila de navegación ni a una URL. Mismo piso que `is_vault_session_id` del
 * backend: `wa_` + `[A-Za-z0-9+_]{1,120}`, con coincidencia completa.
 */
@Serializable
@JvmInline
value class SessionId private constructor(val raw: String) {
    companion object {
        private val FORMAT = Regex("wa_[A-Za-z0-9+_]{1,120}")
        fun parse(raw: String): SessionId? = if (FORMAT.matches(raw)) SessionId(raw) else null
    }
}

/** Id de una orden de Medusa (`order_…`, `draft_…`) o su número. */
@Serializable
@JvmInline
value class OrderId private constructor(val raw: String) {
    companion object {
        private val FORMAT = Regex("[A-Za-z0-9_-]{1,200}")
        fun parse(raw: String): OrderId? = if (FORMAT.matches(raw)) OrderId(raw) else null
    }
}

/** Id de un incendio: `chat:<sesión>` u `order:<orden>`. */
@Serializable
@JvmInline
value class FireId private constructor(val raw: String) {
    companion object {
        private val FORMAT = Regex("(chat|order):[A-Za-z0-9+_-]{1,200}")
        fun parse(raw: String): FireId? = if (FORMAT.matches(raw)) FireId(raw) else null
    }
}
