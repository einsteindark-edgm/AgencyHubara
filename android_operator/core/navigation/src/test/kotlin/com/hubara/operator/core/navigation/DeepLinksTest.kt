package com.hubara.operator.core.navigation

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import org.junit.Test

class DeepLinksTest {
    @Test fun en_vivo_arma_la_pila_de_incendios() {
        assertThat(DeepLinks.parse("hubara://live/wa_test_carlos"))
            .isEqualTo(SyntheticStack(FiresKey, listOf(LiveKey(SessionId.parse("wa_test_carlos")!!))))
    }

    @Test fun chat_y_orden() {
        assertThat(DeepLinks.parse("hubara://chat/wa_test_laura"))
            .isEqualTo(SyntheticStack(InboxKey, listOf(ChatKey(SessionId.parse("wa_test_laura")!!))))
        assertThat(DeepLinks.parse("hubara://order/order_01HX"))
            .isEqualTo(SyntheticStack(OrdersKey, listOf(OrderSheetKey(OrderId.parse("order_01HX")!!))))
    }

    @Test fun una_pantalla_del_servidor_con_sus_parametros() {
        assertThat(DeepLinks.parse("hubara://screen/campana?campaign_id=mkt-1&desde=notificaci%C3%B3n"))
            .isEqualTo(SyntheticStack(InboxKey, listOf(ScreenKey("campana", mapOf("campaign_id" to "mkt-1", "desde" to "notificación")))))
        assertThat(DeepLinks.parse("hubara://screen/ventas")).isEqualTo(SyntheticStack(InboxKey, listOf(ScreenKey("ventas"))))
        assertThat(DeepLinks.screen("campana", mapOf("campaign_id" to "mkt 1"))).isEqualTo("hubara://screen/campana?campaign_id=mkt+1")
        assertThat(DeepLinks.parse("hubara://screen/Ventas")).isNull()
        assertThat(DeepLinks.parse("hubara://screen/..")).isNull()
    }

    // El widget compacto (2×2): tocar un contador abre esa pestaña, en su raíz.
    @Test fun una_pestana() {
        assertThat(DeepLinks.parse("hubara://tab/incendios")).isEqualTo(SyntheticStack(FiresKey, emptyList()))
        assertThat(DeepLinks.parse("hubara://tab/chats")).isEqualTo(SyntheticStack(InboxKey, emptyList()))
        assertThat(DeepLinks.parse("hubara://tab/ordenes")).isEqualTo(SyntheticStack(OrdersKey, emptyList()))
        assertThat(DeepLinks.tab(FiresKey)).isEqualTo("hubara://tab/incendios")
        assertThat(DeepLinks.parse("hubara://tab/borrar")).isNull()
    }

    @Test fun un_link_invalido_no_navega() {
        assertThat(DeepLinks.parse(null)).isNull()
        assertThat(DeepLinks.parse("https://live/wa_test_x")).isNull()
        assertThat(DeepLinks.parse("hubara://live/..%2Fetc")).isNull()
        assertThat(DeepLinks.parse("hubara://live/wa_a/extra")).isNull()
        assertThat(DeepLinks.parse("hubara://borrar/wa_test_x")).isNull()
        assertThat(DeepLinks.parse("hubara://live/")).isNull()
        assertThat(DeepLinks.parse("no es un uri")).isNull()
    }
}
