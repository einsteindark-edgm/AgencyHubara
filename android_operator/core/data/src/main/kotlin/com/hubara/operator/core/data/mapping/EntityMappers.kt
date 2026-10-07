package com.hubara.operator.core.data.mapping

import com.hubara.operator.core.database.ConversationEntity
import com.hubara.operator.core.database.MessageEntity
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId

fun Conversation.toEntity(windowExpiresAtMs: Long? = null) = ConversationEntity(
    sessionId = sessionId.raw,
    phone = phone,
    tag = tag,
    route = route.name,
    lastUpdatedMs = lastUpdatedMs,
    lastInboundMs = lastInboundMs,
    inboundCount = inboundCount,
    orderId = orderRef?.orderId?.raw,
    orderDisplayId = orderRef?.displayId,
    orderPayment = orderRef?.payment?.name,
    orderCount = orderRef?.count ?: 0,
    windowExpiresAtMs = windowExpiresAtMs,
    customerName = customerName,
    lastMessagePreview = lastMessagePreview,
)

fun ConversationEntity.orderRef(): OrderRef? = orderId?.let(OrderId::parse)?.let { id ->
    OrderRef(
        orderId = id,
        displayId = orderDisplayId,
        payment = runCatching { PaymentState.valueOf(orderPayment ?: "") }.getOrDefault(PaymentState.PENDING),
        count = orderCount.coerceAtLeast(1),
    )
}

fun ConversationEntity.toDomain(): Conversation? = SessionId.parse(sessionId)?.let { id ->
    Conversation(
        sessionId = id,
        phone = phone,
        tag = tag,
        route = runCatching { Route.valueOf(route) }.getOrDefault(Route.BOT),
        lastUpdatedMs = lastUpdatedMs,
        lastInboundMs = lastInboundMs,
        inboundCount = inboundCount,
        orderRef = orderRef(),
        customerName = customerName,
        lastMessagePreview = lastMessagePreview,
    )
}

fun Message.toEntity(sessionId: SessionId, position: Int) = MessageEntity(
    sessionId = sessionId.raw,
    key = key,
    position = position,
    author = author.name,
    text = text,
    imageUrl = imageUrl,
    timestampMs = timestampMs,
)

fun MessageEntity.toDomain() = Message(
    key = key,
    author = runCatching { Author.valueOf(author) }.getOrDefault(Author.SYSTEM),
    text = text,
    imageUrl = imageUrl,
    timestampMs = timestampMs,
)
