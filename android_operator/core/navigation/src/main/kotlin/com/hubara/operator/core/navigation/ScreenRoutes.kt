package com.hubara.operator.core.navigation

import androidx.navigation3.runtime.NavKey
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject

/** La pantalla del servidor que arma una clave, con sus parámetros y si se abre como hoja inferior. */
data class ScreenRoute(val screen: String, val params: Map<String, String> = emptyMap(), val sheet: Boolean = false)

/**
 * Toda la app son pantallas del servidor (`android_operator/screens/`), pero las claves de navegación de siempre se
 * quedan: las usan los enlaces de notificaciones (`hubara://chat/…`), el radar, «Volver con …» y las escenas de tablet.
 * Aquí se dice con qué pantalla se arma cada una. Las pestañas de `app.json` reusan las claves de primer nivel.
 */
object ScreenRoutes {
    private val tabs: Map<String, NavKey> = mapOf("chats" to InboxKey, "incendios" to FiresKey, "ordenes" to OrdersKey)

    fun of(key: NavKey): ScreenRoute? = when (key) {
        InboxKey -> ScreenRoute("chats")
        FiresKey -> ScreenRoute("incendios")
        OrdersKey -> ScreenRoute("ordenes")
        is ChatKey -> ScreenRoute("chat", mapOf("session" to key.session.raw))
        // El mismo chat abierto desde Incendios (su pila): «live» para que la pantalla lo sepa si le importa.
        is LiveKey -> ScreenRoute("chat", mapOf("session" to key.session.raw, "live" to "true"))
        is OrderSheetKey -> ScreenRoute("pedido", mapOf("order_id" to key.order.raw), sheet = true)
        is ActionPaletteKey -> ScreenRoute("acciones", mapOf("session" to key.session.raw), sheet = true)
        is TemplateSheetKey -> ScreenRoute("plantillas", mapOf("session" to key.session.raw), sheet = true)
        is ScreenKey -> ScreenRoute(key.screen, key.params)
        is ScreenSheetKey -> ScreenRoute(key.screen, key.params, sheet = true)
        else -> null
    }

    /** La clave de una pestaña de `app.json`: las de siempre para chats, incendios y órdenes; si no, la de la pantalla. */
    fun tab(screen: String): NavKey = tabs[screen] ?: ScreenKey(screen)
}

/**
 * A dónde lleva un incendio del radar: todo chat se abre «en vivo» (LiveKey, de la pila de Incendios: el mismo chat no
 * queda abierto en dos pilas); una orden con id abre su ficha y, sin id, su chat. null si no hay a dónde ir.
 */
fun fireDestination(fire: Fire): NavKey? {
    val session = fire.subject.sessionId
    val order = (fire.subject as? FireSubject.Order)?.orderId
    return when (fire.primaryAction.name) {
        "open_order" -> order?.let(::OrderSheetKey) ?: session?.let(::LiveKey)
        else -> session?.let(::LiveKey)
    }
}
