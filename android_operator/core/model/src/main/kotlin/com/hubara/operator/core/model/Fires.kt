package com.hubara.operator.core.model

/** Gravedad de un incendio. El orden de declaración es el orden del feed. */
enum class Severity { GRAVE, HOY, ESPERA }

enum class FireKind {
    WANTS_HUMAN, ANGRY, BOT_STUCK, SALE_AT_RISK, HEALTH, REPUTATION,
    ORDER_PROBLEM, ASKING_STATUS, PAYMENT_PROOF, DELAYED, PRAISE, OTHER,
}

sealed interface FireSubject {
    val sessionId: SessionId?

    data class Chat(override val sessionId: SessionId) : FireSubject
    data class Order(val orderId: OrderId?, override val sessionId: SessionId?) : FireSubject
}

data class Fire(
    val id: FireId,
    val subject: FireSubject,
    val severity: Severity,
    val kind: FireKind,
    val gettingWorse: Boolean,
    val title: String,
    val subtitle: String,
    val primaryAction: ActionRef,
    val decidedBy: String,
    val updatedMs: Long,
)

object FireOrdering {
    /** Grave primero; dentro de la misma gravedad, lo que empeora; después lo más viejo. */
    val FEED: Comparator<Fire> = compareBy<Fire>({ it.severity.ordinal }, { !it.gettingWorse }, { it.updatedMs })

    /** El radar solo muestra lo grave que el operador no ocultó. */
    fun radar(fires: List<Fire>, hidden: Set<FireId>): List<Fire> =
        fires.filter { it.severity == Severity.GRAVE && it.id !in hidden }.sortedWith(FEED)
}
