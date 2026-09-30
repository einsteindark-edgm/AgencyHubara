package com.hubara.operator.core.navigation

import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import java.net.URI
import java.net.URISyntaxException

/**
 * Los links que abren la notificación y el widget: `hubara://live/{id}`, `hubara://chat/{id}` y
 * `hubara://order/{id}`. Llegan siempre en un intent explícito; igual se validan (MainActivity está
 * exportada por ser el launcher). Un link inválido no navega.
 */
object DeepLinks {
    fun parse(raw: String?): SyntheticStack? {
        val uri = try {
            URI(raw ?: return null)
        } catch (_: URISyntaxException) {
            return null
        }
        if (uri.scheme != "hubara") return null
        val segments = uri.rawPath.orEmpty().trim('/').split('/').filter { it.isNotEmpty() }
        if (segments.size != 1) return null
        val id = segments.single()
        return when (uri.host) {
            "live" -> SessionId.parse(id)?.let { SyntheticStack(FiresKey, listOf(LiveKey(it))) }
            "chat" -> SessionId.parse(id)?.let { SyntheticStack(InboxKey, listOf(ChatKey(it))) }
            "order" -> OrderId.parse(id)?.let { SyntheticStack(OrdersKey, listOf(OrderSheetKey(it))) }
            else -> null
        }
    }

    fun live(session: SessionId) = "hubara://live/${session.raw}"
    fun chat(session: SessionId) = "hubara://chat/${session.raw}"
    fun order(order: OrderId) = "hubara://order/${order.raw}"
}
