package com.hubara.operator.core.network

import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.ChatDetail
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.OrderDetail
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderItem
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.OrderSummary
import com.hubara.operator.core.model.PayStatus
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Prominence
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.model.ShippingAddress
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.model.SuggestionSet
import com.hubara.operator.core.model.SuggestionTone
import com.hubara.operator.core.network.dto.ActionRefDto
import com.hubara.operator.core.network.dto.ChatMessageDto
import com.hubara.operator.core.network.dto.ChatSessionDto
import com.hubara.operator.core.network.dto.FireDto
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.OrderDetailDto
import com.hubara.operator.core.network.dto.OrderRefDto
import com.hubara.operator.core.network.dto.OrderSummaryDto
import com.hubara.operator.core.network.dto.SessionDetailsDto
import com.hubara.operator.core.network.dto.SuggestionsDto
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull

// Todo lo que llega del backend se valida acá: un id inválido descarta la fila, un enum nuevo cae en
// su valor por defecto. Ninguna pantalla ve un DTO.

fun OrderRefDto.toDomain(): OrderRef? = OrderId.parse(orderId)?.let { id ->
    OrderRef(
        orderId = id,
        displayId = displayId?.removePrefix("#"),
        payment = when (payment) {
            "confirmed" -> PaymentState.CONFIRMED
            "cancelled" -> PaymentState.CANCELLED
            else -> PaymentState.PENDING
        },
        count = count.coerceAtLeast(1),
    )
}

fun ChatSessionDto.toDomain(): Conversation? = SessionId.parse(sessionId)?.let { id ->
    Conversation(
        sessionId = id,
        phone = phoneNumber,
        tag = tag,
        route = Route.fromBackend(activeAgentRoute),
        lastUpdatedMs = (lastUpdatedTimestamp * 1000).toLong(),
        lastInboundMs = lastInboundMs,
        inboundCount = inboundCount.coerceAtLeast(0),
        orderRef = orderRef?.toDomain(),
        customerName = customerName?.trim()?.takeIf { it.isNotEmpty() },
        lastMessagePreview = lastMessagePreview?.trim()?.takeIf { it.isNotEmpty() },
    )
}

/** Tipos de evento del historial que el operador no necesita ver en el chat. */
private val HIDDEN_UI_TYPES = setOf("agent_tool_call", "tool_execution_result")

internal fun parseTimestampMs(raw: JsonElement?): Long? {
    val p = raw as? JsonPrimitive ?: return null
    if (p.isString) {
        return try {
            OffsetDateTime.parse(p.content).toInstant().toEpochMilli()
        } catch (_: DateTimeParseException) {
            null
        }
    }
    val n = p.doubleOrNull ?: return null
    return if (n < 1e12) (n * 1000).toLong() else n.toLong()   // segundos o milisegundos
}

private fun ChatMessageDto.author(): Author = when (uiType) {
    "user_message" -> Author.CUSTOMER
    "human_message" -> Author.HUMAN
    "agent_message" -> if (sender == "human") Author.HUMAN else Author.BOT
    else -> Author.SYSTEM
}

fun SessionDetailsDto.toDomain(): ChatDetail? {
    val id = SessionId.parse(sessionId) ?: return null
    val usedKeys = HashSet<String>()
    val messages = messages.withIndex()
        .filter { (_, m) -> m.uiType !in HIDDEN_UI_TYPES }
        .map { (index, m) ->
            val ts = parseTimestampMs(m.timestamp)
            // El historial solo crece al final: el índice desde el inicio es estable. Un wamid
            // repetido (FakeSend de desarrollo) lleva el índice para no chocar en Room ni en la lista.
            val base = m.wamid ?: "i$index:${ts ?: 0}"
            Message(
                key = if (usedKeys.add(base)) base else "$base#$index",
                author = m.author(),
                text = m.content,
                imageUrl = m.imageUrl,
                timestampMs = ts,
                sentAtMs = parseTimestampMs(m.sentAt),
                arrivedAfterWindow = (m.arrivedAfterWindow as? JsonPrimitive)?.booleanOrNull == true,
            )
        }
    return ChatDetail(
        sessionId = id,
        phone = phoneNumber,
        route = Route.fromBackend(activeAgentRoute),
        tag = tag,
        windowExpiresAtMs = serviceWindowExpiresAtMs,
        orderRef = orderRef?.toDomain(),
        messages = messages,
    )
}

fun ActionRefDto.toDomain() = ActionRef(name = name, args = args)

fun com.hubara.operator.core.network.dto.TemplateDto.toDomain() = com.hubara.operator.core.model.Template(
    name = name,
    body = body,
    variables = variables.map { com.hubara.operator.core.model.TemplateVariable(it.name, it.description, it.maxLength) },
    isDefault = isDefault,
    needsImage = headerFormat == "image",
)

fun SuggestionsDto.toDomain(): SuggestionSet? = SessionId.parse(sessionId)?.let { id ->
    SuggestionSet(
        sessionId = id,
        version = version,
        decidedBy = decidedBy,
        stage = stage,
        windowOpen = windowOpen,
        humanInControl = inControl == "human",
        suggestions = suggestions.map {
            Suggestion(
                id = it.id,
                label = it.label,
                prominence = if (it.prominence == "primary") Prominence.PRIMARY else Prominence.NORMAL,
                action = it.action.toDomain(),
                editable = it.editable,
                tone = if (it.tone == "order") SuggestionTone.ORDER else SuggestionTone.NORMAL,
                opens = it.opens?.takeIf(String::isNotBlank),
            )
        },
    )
}

private fun severity(raw: String): Severity = when (raw) {
    "grave" -> Severity.GRAVE
    "hoy" -> Severity.HOY
    else -> Severity.ESPERA
}

private fun fireKind(raw: String): FireKind = when (raw) {
    "wants_human" -> FireKind.WANTS_HUMAN
    "angry" -> FireKind.ANGRY
    "bot_stuck" -> FireKind.BOT_STUCK
    "sale_at_risk" -> FireKind.SALE_AT_RISK
    "health" -> FireKind.HEALTH
    "reputation" -> FireKind.REPUTATION
    "order_problem" -> FireKind.ORDER_PROBLEM
    "asking_status" -> FireKind.ASKING_STATUS
    "payment_proof" -> FireKind.PAYMENT_PROOF
    "delayed" -> FireKind.DELAYED
    "praise" -> FireKind.PRAISE
    else -> FireKind.OTHER
}

fun FireDto.toDomain(decidedBy: String): Fire? {
    val id = FireId.parse(fireId) ?: return null
    val session = subject.sessionId?.let(SessionId::parse)
    val subjectDomain = when (subject.kind) {
        "order" -> FireSubject.Order(orderId = subject.orderId?.let(OrderId::parse), sessionId = session)
        else -> FireSubject.Chat(session ?: return null)
    }
    return Fire(
        id = id,
        subject = subjectDomain,
        severity = severity(severity),
        kind = fireKind(kind),
        gettingWorse = gettingWorse,
        title = title,
        subtitle = subtitle,
        primaryAction = primaryAction.toDomain(),
        decidedBy = decidedBy,
        updatedMs = updatedMs,
    )
}

fun FiresDto.toDomain(): List<Fire> = fires.mapNotNull { it.toDomain(decidedBy) }

private fun payStatus(raw: String): PayStatus = when (raw) {
    "paid" -> PayStatus.PAID
    "partial" -> PayStatus.PARTIAL
    "refund" -> PayStatus.REFUND
    else -> PayStatus.PENDING
}

fun OrderSummaryDto.toDomain(): OrderSummary? = OrderId.parse(id)?.let { orderId ->
    OrderSummary(
        id = orderId,
        displayId = displayId.removePrefix("#"),   // órdenes manda "#41"; la UI pone el # una vez
        customer = customer,
        city = city,
        stage = OrderStage.fromBackend(status) ?: OrderStage.NEW,
        payStatus = payStatus(payStatus),
        totalCop = totalCop,
        overdue = overdue,
        updatedAtMs = updatedAtMs,
    )
}

fun OrderDetailDto.toDomain(): OrderDetail? {
    val s = summary.toDomain() ?: return null
    return OrderDetail(
        summary = s,
        items = itemsDetail.map {
            OrderItem(it.title, it.variantLabel, it.quantity, it.unitPriceCop, it.totalCop, it.thumbnail)
        },
        address = shippingAddress?.let {
            ShippingAddress(
                receiver = listOfNotNull(it.firstName, it.lastName).joinToString(" ").ifBlank { null },
                phone = it.phone,
                address = it.address1,
                neighborhood = it.address2,     // el barrio viaja en address_2
                city = it.city,
            )
        },
        subtotalCop = subtotalCop,
        shippingCop = shippingCop,
        discountCop = discountTotalCop,
        paymentLabel = paymentMethodLabel,
        missing = dataCompletenessMissing,
    )
}
