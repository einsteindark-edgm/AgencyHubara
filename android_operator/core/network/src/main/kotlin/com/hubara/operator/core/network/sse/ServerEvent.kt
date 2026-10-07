package com.hubara.operator.core.network.sse

import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.dto.DashboardEventDto
import com.hubara.operator.core.network.dto.SessionsResponse
import com.hubara.operator.core.network.toDomain
import kotlinx.serialization.SerializationException

/** Lo que la app entiende del SSE del dashboard. Todo lo demás es `Unknown` y se ignora. */
sealed interface ServerEvent {
    data class SessionsSnapshot(val conversations: List<Conversation>) : ServerEvent
    data class SessionUpdated(val sessionId: SessionId) : ServerEvent
    data object OrdersChanged : ServerEvent
    data class Unknown(val domain: String, val type: String) : ServerEvent
}

/** Decodifica el `data:` de un evento. `null` si el evento está roto o trae un id inválido. */
fun decodeServerEvent(data: String): ServerEvent? {
    val dto = try {
        OperatorJson.decodeFromString<DashboardEventDto>(data)
    } catch (_: SerializationException) {
        return null
    } catch (_: IllegalArgumentException) {
        return null
    }
    return when {
        dto.domain == "chats" && dto.type == "sessions_snapshot" -> {
            val payload = dto.payload ?: return null
            val sessions = runCatching { OperatorJson.decodeFromJsonElement(SessionsResponse.serializer(), payload) }
                .getOrNull() ?: return null
            ServerEvent.SessionsSnapshot(sessions.sessions.mapNotNull { it.toDomain() })
        }
        dto.domain == "chats" && dto.type == "session_updated" ->
            SessionId.parse(dto.id.orEmpty())?.let(ServerEvent::SessionUpdated)
        dto.domain == "orders" -> ServerEvent.OrdersChanged
        else -> ServerEvent.Unknown(dto.domain, dto.type)
    }
}
