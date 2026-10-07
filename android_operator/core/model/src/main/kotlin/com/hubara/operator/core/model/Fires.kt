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

/**
 * Los incendios graves nuevos que el radar muestra un momento sin que el operador lo pida. Si llegan varios
 * seguidos se juntan (el más nuevo arriba) y se pliegan juntos al chip: ninguno reemplaza al anterior.
 */
data class RadarBurst(
    val fires: List<Fire> = emptyList(),
    /** Lo que ya está en el radar y no se vuelve a anunciar. */
    val seen: Set<FireId> = emptySet(),
    /** Sube con cada llegada: la espera antes de plegarse vuelve a empezar. */
    val arrivals: Int = 0,
) {
    /**
     * Con el radar desplegado no aparece nada solo: el operador ya está mirando la lista. Lo que sale del
     * radar (resuelto u oculto) sale de la ráfaga y, si vuelve a prender, se anuncia otra vez.
     */
    fun onRadar(radar: List<Fire>, expanded: Boolean): RadarBurst {
        // Dos que llegan en la misma recarga: arriba el del mensaje más reciente.
        val fresh = radar.filter { it.id !in seen }.sortedByDescending { it.updatedMs }
        val stillThere = fires.mapNotNull { shown -> radar.firstOrNull { it.id == shown.id } }
        val seenNow = radar.mapTo(HashSet()) { it.id }
        return when {
            expanded -> RadarBurst(emptyList(), seenNow, arrivals)
            fresh.isEmpty() -> RadarBurst(stillThere, seenNow, arrivals)
            else -> RadarBurst(fresh + stillThere, seenNow, arrivals + 1)
        }
    }

    fun folded(): RadarBurst = copy(fires = emptyList())

    fun visible(max: Int = MAX_VISIBLE): List<Fire> = fires.take(max)

    fun overflow(max: Int = MAX_VISIBLE): Int = (fires.size - max).coerceAtLeast(0)

    companion object {
        const val MAX_VISIBLE = 3
    }
}
