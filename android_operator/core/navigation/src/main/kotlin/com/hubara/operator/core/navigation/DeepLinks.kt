package com.hubara.operator.core.navigation

import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder
import java.net.URLEncoder

/** Ids de las pantallas del servidor: los mismos que acepta `ScreenStore` (minúsculas, números, guion bajo). */
private val SCREEN_ID = Regex("^[a-z0-9_]{1,64}$")

/**
 * Los links que abren la notificación y el widget: `hubara://live/{id}`, `hubara://chat/{id}`,
 * `hubara://order/{id}` y `hubara://screen/{pantalla}?param=valor` (una pantalla del servidor). Llegan siempre en un intent explícito; igual se validan (MainActivity está
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
            "screen" -> id.takeIf { SCREEN_ID.matches(it) }?.let { SyntheticStack(InboxKey, listOf(ScreenKey(it, query(uri.rawQuery)))) }
            else -> null
        }
    }

    fun live(session: SessionId) = "hubara://live/${session.raw}"
    fun chat(session: SessionId) = "hubara://chat/${session.raw}"
    fun order(order: OrderId) = "hubara://order/${order.raw}"

    fun screen(id: String, params: Map<String, String> = emptyMap()): String =
        "hubara://screen/$id" + if (params.isEmpty()) "" else "?" + params.entries.joinToString("&") { (k, v) ->
            "${URLEncoder.encode(k, "UTF-8")}=${URLEncoder.encode(v, "UTF-8")}"
        }

    private fun query(raw: String?): Map<String, String> = raw.orEmpty().split('&').filter { it.isNotEmpty() }.associate { pair ->
        val (k, v) = pair.split('=', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8")
    }
}
