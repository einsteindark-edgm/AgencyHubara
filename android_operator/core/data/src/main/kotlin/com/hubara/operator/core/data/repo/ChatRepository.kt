package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.mapping.orderRef
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.outbox.photoPreviewUrl
import com.hubara.operator.core.data.mapping.toDomain
import com.hubara.operator.core.data.mapping.toEntity
import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.database.ConversationDao
import com.hubara.operator.core.database.ConversationEntity
import com.hubara.operator.core.database.DraftDao
import com.hubara.operator.core.database.DraftEntity
import com.hubara.operator.core.database.MessageDao
import com.hubara.operator.core.database.OutboxDao
import com.hubara.operator.core.database.OutboxEntity
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.InterveneRequest
import com.hubara.operator.core.network.dto.ReturnToBotRequest
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine

/** La clave de las burbujas que todavía están en el outbox: solo esas pueden mostrar una foto del teléfono. */
const val PENDING_KEY_PREFIX = "outbox:"

/** Una acción del operador que todavía está en el outbox (para la barra de deshacer). */
data class PendingAction(
    val id: String,
    val label: String,
    val state: OutboxState,
    val createdMs: Long,
    val error: String?,
)

enum class OutboxState { PENDING_UNDO, QUEUED, SENDING, SENT, FAILED;
    companion object {
        fun fromDb(raw: String) = when (raw) {
            "pending_undo" -> PENDING_UNDO
            "sending" -> SENDING
            "sent" -> SENT
            "failed" -> FAILED
            else -> QUEUED
        }
    }
}

/** Todo lo que pinta la pantalla del chat, armado desde Room. */
data class ChatView(
    val sessionId: SessionId,
    val phone: String,
    /** Nombre de perfil de WhatsApp; null = el encabezado muestra el número. */
    val customerName: String? = null,
    val route: Route,
    val orderRef: OrderRef?,
    val windowExpiresAtMs: Long?,
    val messages: List<Message>,
    val pendingActions: List<PendingAction>,
)

@Singleton
class ChatRepository @Inject constructor(
    private val api: OperatorApi,
    private val conversations: ConversationDao,
    private val messages: MessageDao,
    private val outbox: OutboxDao,
    private val drafts: DraftDao,
) {
    fun observeChat(sessionId: SessionId): Flow<ChatView> = combine(
        conversations.observe(sessionId.raw),
        messages.observe(sessionId.raw),
        outbox.observeUnsent(sessionId.raw),
    ) { conv, rows, pending ->
        // Textos y fotos salen como burbuja «enviando…» en el historial; las fotos que fallaron, además, en la barra
        // de abajo con «Reintentar» y «Descartar».
        val bubbles = pending.filter { it.kind == "text" || it.kind == OutboxRepository.KIND_PHOTO }.map { it.toPendingMessage() }
        ChatView(
            sessionId = sessionId,
            phone = conv?.phone.orEmpty(),
            customerName = conv?.customerName,
            route = conv?.route?.let { runCatching { Route.valueOf(it) }.getOrNull() } ?: Route.BOT,
            orderRef = conv?.orderRef(),
            windowExpiresAtMs = conv?.windowExpiresAtMs,
            messages = rows.map { it.toDomain() } + bubbles,
            pendingActions = pending.filter { it.kind != "text" && (it.kind != OutboxRepository.KIND_PHOTO || it.state == "failed") }.map {
                PendingAction(it.clientActionId, it.label, OutboxState.fromDb(it.state), it.createdMs, it.error)
            },
        )
    }

    /** Trae el historial y los datos del chat (ruta, pedido, ventana de 24 h) y los guarda. */
    suspend fun refresh(sessionId: SessionId): Result<Unit> = safeCall {
        val detail = api.session(sessionId.raw).toDomain() ?: error("sesión inválida")
        val existing = conversations.get(sessionId.raw)
        conversations.upsert(listOf(
            (existing ?: ConversationEntity(sessionId.raw, detail.phone, detail.tag, detail.route.name, 0, null, 0, null, null, null, 0))
                .copy(
                    phone = detail.phone,
                    tag = detail.tag,
                    route = detail.route.name,
                    orderId = detail.orderRef?.orderId?.raw,
                    orderDisplayId = detail.orderRef?.displayId,
                    orderPayment = detail.orderRef?.payment?.name,
                    orderCount = detail.orderRef?.count ?: 0,
                    windowExpiresAtMs = detail.windowExpiresAtMs,
                ),
        ))
        messages.replaceSession(sessionId.raw, detail.messages.mapIndexed { i, m -> m.toEntity(sessionId, i) })
    }

    suspend fun intervene(sessionId: SessionId): Result<Unit> = safeCall {
        val r = api.intervene(sessionId.raw, InterveneRequest(motivo = "Intervención desde la app"))
        conversations.setRoute(sessionId.raw, Route.fromBackend(r.activeRoute).name)
    }

    suspend fun returnToBot(sessionId: SessionId): Result<Unit> = safeCall {
        val r = api.returnToBot(sessionId.raw, ReturnToBotRequest(targetRoute = "ventas"))
        conversations.setRoute(sessionId.raw, Route.fromBackend(r.activeRoute).name)
    }

    suspend fun loadDraft(sessionId: SessionId): String = drafts.get(sessionId.raw)?.text.orEmpty()

    suspend fun saveDraft(sessionId: SessionId, text: String, nowMs: Long) {
        if (text.isBlank()) drafts.delete(sessionId.raw) else drafts.upsert(DraftEntity(sessionId.raw, text, nowMs))
    }

    private fun OutboxEntity.toPendingMessage() = Message(
        key = PENDING_KEY_PREFIX + clientActionId,
        author = Author.HUMAN,
        text = text,
        imageUrl = if (kind == OutboxRepository.KIND_PHOTO) photoPreviewUrl(argsJson) else null,
        timestampMs = createdMs,
        state = if (state == "failed") DeliveryState.FAILED else DeliveryState.PENDING,
    )
}
