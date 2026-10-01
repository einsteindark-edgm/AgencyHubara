package com.hubara.operator.core.navigation

import androidx.navigation3.runtime.NavKey
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import kotlinx.serialization.Serializable

// Las claves de la pila viven en un solo módulo: cada funcionalidad navega a otra sin conocer su
// implementación, y la pila completa se guarda (rotación, muerte del proceso) por kotlinx.serialization.

/** Raíz de Chats: la pila de inicio. */
@Serializable data object InboxKey : NavKey

@Serializable data class ChatKey(val session: SessionId) : NavKey

/** El mismo chat, abierto para mirar al bot en vivo (desde Incendios, el widget o una notificación). */
@Serializable data class LiveKey(val session: SessionId) : NavKey

/** Raíz de Incendios. */
@Serializable data object FiresKey : NavKey

/** Raíz de Órdenes. */
@Serializable data object OrdersKey : NavKey

/** La ficha de la orden: una hoja inferior sobre lo que había debajo. */
@Serializable data class OrderSheetKey(val order: OrderId) : NavKey

/** «+ Más»: todas las acciones del chat, en una hoja inferior. */
@Serializable data class ActionPaletteKey(val session: SessionId) : NavKey

/** Reactivar la conversación con una plantilla (ventana de 24 h cerrada), en una hoja inferior. */
@Serializable data class TemplateSheetKey(val session: SessionId) : NavKey

/** Los tres destinos de primer nivel, cada uno con su pila. */
/**
 * Escena de lista + detalle de cada pila (pantallas ≥ 840 dp). Navigation 3 junta en una escena las entradas seguidas
 * con la misma clave: con una por pila, Incendios nunca muestra al lado un chat de la pestaña Chats.
 */
object SceneKeys {
    const val CHATS = "chats"
    const val FIRES = "fires"
    const val ORDERS = "orders"
}

object TopLevel {
    val roots: List<NavKey> = listOf(InboxKey, FiresKey, OrdersKey)
}

/** Una pila armada para un deep link: atrás recorre [keys] y termina en [root], no sale de la app. */
data class SyntheticStack(val root: NavKey, val keys: List<NavKey>)
