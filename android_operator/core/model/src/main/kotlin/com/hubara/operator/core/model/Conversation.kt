package com.hubara.operator.core.model

/** Quién atiende la conversación. El backend manda `ventas`, `remarketing` o `humano`. */
enum class Route {
    BOT, HUMAN;

    companion object {
        fun fromBackend(raw: String): Route = if (raw == "humano") HUMAN else BOT
    }
}

enum class PaymentState { PENDING, CONFIRMED, CANCELLED }

/** Pedido real asignado a la conversación (un borrador no cuenta). */
data class OrderRef(
    val orderId: OrderId,
    val displayId: String?,
    val payment: PaymentState,
    val count: Int,
)

data class Conversation(
    val sessionId: SessionId,
    val phone: String,
    val tag: String,
    val route: Route,
    val lastUpdatedMs: Long,
    val lastInboundMs: Long?,
    val inboundCount: Int,
    val orderRef: OrderRef?,
    /** Nombre de perfil de WhatsApp; null = mostrar el número. */
    val customerName: String? = null,
    val lastMessagePreview: String? = null,
)

enum class Author { CUSTOMER, BOT, HUMAN, SYSTEM }

data class Message(
    /** Clave estable para la lista: el wamid o un hash de su contenido. */
    val key: String,
    val author: Author,
    val text: String?,
    val imageUrl: String?,
    val timestampMs: Long?,
    val state: DeliveryState = DeliveryState.SENT,
)

/** Estado de un mensaje del operador que pasa por el outbox. */
enum class DeliveryState { SENT, PENDING, FAILED }

data class ChatDetail(
    val sessionId: SessionId,
    val phone: String,
    val route: Route,
    val tag: String,
    val windowExpiresAtMs: Long?,
    val orderRef: OrderRef?,
    val messages: List<Message>,
) {
    fun windowOpen(nowMs: Long): Boolean = windowExpiresAtMs == null || windowExpiresAtMs > nowMs
}
