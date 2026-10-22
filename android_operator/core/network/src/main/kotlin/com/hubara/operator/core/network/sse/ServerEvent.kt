package com.hubara.operator.core.network.sse

import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.dto.DashboardEventDto
import com.hubara.operator.core.network.dto.SessionsDeltaDto
import com.hubara.operator.core.network.dto.SessionsResponse
import com.hubara.operator.core.network.toDomain
import kotlinx.serialization.SerializationException

/** Lo que la app entiende del SSE del dashboard. Todo lo demás es `Unknown` y se ignora. */
sealed interface ServerEvent {
    data class SessionsSnapshot(val conversations: List<Conversation>) : ServerEvent

    /** Solo los chats que cambiaron desde lo último que recibió esta conexión, y los que ya no están (`inbox=delta`). */
    data class SessionsDelta(val conversations: List<Conversation>, val removed: List<SessionId>) : ServerEvent
    data class SessionUpdated(val sessionId: SessionId) : ServerEvent
    data object OrdersChanged : ServerEvent
    data class Unknown(val domain: String, val type: String) : ServerEvent
}

/** Un evento y la hora en que salió del servidor (`ts_ms`; null si no la trae), para medir cuánto tardó en llegar. */
data class ServerFrame(val event: ServerEvent, val sentAtMs: Long?)

/** Decodifica el `data:` de un evento. `null` si el evento está roto o trae un id inválido. */
fun decodeServerEvent(data: String): ServerEvent? = decodeServerFrame(data)?.event

fun decodeServerFrame(data: String): ServerFrame? {
    val dto = try {
        OperatorJson.decodeFromString<DashboardEventDto>(data)
    } catch (_: SerializationException) {
        return null
    } catch (_: IllegalArgumentException) {
        return null
    }
    val event = when {
        dto.domain == "chats" && dto.type == "sessions_snapshot" -> {
            val payload = dto.payload ?: return null
            val sessions = runCatching { OperatorJson.decodeFromJsonElement(SessionsResponse.serializer(), payload) }
                .getOrNull() ?: return null
            ServerEvent.SessionsSnapshot(sessions.sessions.mapNotNull { it.toDomain() })
        }
        dto.domain == "chats" && dto.type == "sessions_delta" -> {
            val payload = dto.payload ?: return null
            val delta = runCatching { OperatorJson.decodeFromJsonElement(SessionsDeltaDto.serializer(), payload) }
                .getOrNull() ?: return null
            ServerEvent.SessionsDelta(delta.sessions.mapNotNull { it.toDomain() }, delta.removed.mapNotNull(SessionId::parse))
        }
        dto.domain == "chats" && dto.type == "session_updated" ->
            SessionId.parse(dto.id.orEmpty())?.let(ServerEvent::SessionUpdated) ?: return null
        dto.domain == "orders" -> ServerEvent.OrdersChanged
        else -> ServerEvent.Unknown(dto.domain, dto.type)
    }
    return ServerFrame(event, dto.tsMs.takeIf { it > 0 })
}
