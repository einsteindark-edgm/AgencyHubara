package com.hubara.operator.core.navigation

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import org.junit.Test

/**
 * Toda la app son pantallas del servidor, pero las claves de navegación de siempre se quedan (enlaces de notificaciones,
 * radar, «Volver con …»): cada una se arma con su pantalla. Las pestañas de `app.json` usan esas mismas claves.
 */
class ScreenRoutesTest {
    private val laura = SessionId.parse("wa_test_laura")!!
    private val order = OrderId.parse("order_01HX")!!

    @Test fun cada_clave_se_arma_con_su_pantalla_y_sus_parametros() {
        assertThat(ScreenRoutes.of(InboxKey)).isEqualTo(ScreenRoute("chats"))
        assertThat(ScreenRoutes.of(FiresKey)).isEqualTo(ScreenRoute("incendios"))
        assertThat(ScreenRoutes.of(OrdersKey)).isEqualTo(ScreenRoute("ordenes"))
        assertThat(ScreenRoutes.of(ChatKey(laura))).isEqualTo(ScreenRoute("chat", mapOf("session" to "wa_test_laura")))
        assertThat(ScreenRoutes.of(LiveKey(laura))).isEqualTo(ScreenRoute("chat", mapOf("session" to "wa_test_laura", "live" to "true")))
        assertThat(ScreenRoutes.of(OrderSheetKey(order))).isEqualTo(ScreenRoute("pedido", mapOf("order_id" to "order_01HX"), sheet = true))
        assertThat(ScreenRoutes.of(ActionPaletteKey(laura))).isEqualTo(ScreenRoute("acciones", mapOf("session" to "wa_test_laura"), sheet = true))
        assertThat(ScreenRoutes.of(TemplateSheetKey(laura))).isEqualTo(ScreenRoute("plantillas", mapOf("session" to "wa_test_laura"), sheet = true))
        assertThat(ScreenRoutes.of(ScreenKey("ventas"))).isEqualTo(ScreenRoute("ventas"))
    }

    @Test fun una_pestana_del_manifiesto_usa_la_clave_de_siempre() {
        assertThat(ScreenRoutes.tab("chats")).isEqualTo(InboxKey)
        assertThat(ScreenRoutes.tab("incendios")).isEqualTo(FiresKey)
        assertThat(ScreenRoutes.tab("ordenes")).isEqualTo(OrdersKey)
        assertThat(ScreenRoutes.tab("mas")).isEqualTo(ScreenKey("mas"))
    }

    private fun fire(subject: FireSubject, action: String) = Fire(
        FireId.parse("chat:wa_test_laura")!!, subject, Severity.GRAVE, FireKind.OTHER, false, "t", "", ActionRef(action), "rules", 0,
    )

    // Desde el radar todo chat se abre «en vivo» (pila de Incendios); una orden con id abre su ficha.
    @Test fun a_donde_lleva_un_incendio_del_radar() {
        assertThat(fireDestination(fire(FireSubject.Chat(laura), "open_chat"))).isEqualTo(LiveKey(laura))
        assertThat(fireDestination(fire(FireSubject.Order(order, laura), "open_order"))).isEqualTo(OrderSheetKey(order))
        assertThat(fireDestination(fire(FireSubject.Order(null, laura), "open_order"))).isEqualTo(LiveKey(laura))
        assertThat(fireDestination(fire(FireSubject.Order(null, null), "open_order"))).isNull()
    }
}
