package com.hubara.operator.core.navigation

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.navigation3.runtime.NavKey
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import org.junit.Test

class NavigatorTest {
    private val laura = SessionId.parse("wa_test_laura")!!
    private val carlos = SessionId.parse("wa_test_carlos")!!

    private fun navigator(): Pair<Navigator, NavigationState> {
        val state = NavigationState(
            startRoute = InboxKey,
            topLevelRoute = mutableStateOf<NavKey>(InboxKey),
            backStacks = TopLevel.roots.associateWith { mutableStateListOf(it) },
        )
        return Navigator(state) to state
    }

    @Test fun una_pestana_del_servidor_tiene_su_propia_pila() {
        val mas = ScreenKey("mas")
        val state = NavigationState(
            startRoute = InboxKey,
            topLevelRoute = mutableStateOf<NavKey>(InboxKey),
            backStacks = (TopLevel.roots + mas).associateWith { mutableStateListOf(it) },
        )
        val nav = Navigator(state)
        nav.navigate(ScreenKey("mas"))
        assertThat(state.topLevelRoute).isEqualTo(mas)
        nav.navigate(ScreenKey("campana", mapOf("campaign_id" to "mkt-1")))
        nav.navigate(OrderSheetKey(OrderId.parse("order_01")!!))
        assertThat(state.backStacks.getValue(mas)).containsExactly(
            mas, ScreenKey("campana", mapOf("campaign_id" to "mkt-1")), OrderSheetKey(OrderId.parse("order_01")!!),
        ).inOrder()
        // Otra pantalla del servidor con otros parámetros es otra entrada (no la misma).
        nav.navigate(ScreenKey("campana", mapOf("campaign_id" to "mkt-2")))
        assertThat(state.backStacks.getValue(mas)).hasSize(4)
    }

    @Test fun el_radar_lleva_a_incendios_y_volver_a_laura_encuentra_su_chat_intacto() {
        val (nav, state) = navigator()
        nav.navigate(ChatKey(laura))
        nav.navigate(FiresKey)
        nav.navigate(LiveKey(carlos))
        nav.navigate(ChatKey(carlos))
        assertThat(state.topLevelRoute).isEqualTo(FiresKey)
        assertThat(nav.returnTarget()).isEqualTo(laura)

        nav.navigate(InboxKey)
        assertThat(state.visibleKeys().last()).isEqualTo(ChatKey(laura))
        assertThat(nav.returnTarget()).isNull()
    }

    @Test fun atras_desde_la_raiz_de_otra_pila_vuelve_a_chats_y_desde_chats_sale() {
        val (nav, state) = navigator()
        nav.navigate(OrdersKey)
        assertThat(nav.goBack()).isTrue()
        assertThat(state.topLevelRoute).isEqualTo(InboxKey)
        assertThat(nav.goBack()).isFalse()
    }

    @Test fun la_pila_sintetica_del_deep_link_reemplaza_la_de_incendios() {
        val (nav, state) = navigator()
        nav.navigate(FiresKey)
        nav.navigate(LiveKey(laura))
        nav.apply(SyntheticStack(FiresKey, listOf(LiveKey(carlos))))
        assertThat(state.topLevelRoute).isEqualTo(FiresKey)
        assertThat(state.backStacks.getValue(FiresKey)).containsExactly(FiresKey, LiveKey(carlos)).inOrder()
        assertThat(nav.goBack()).isTrue()
        assertThat(state.visibleKeys().last()).isEqualTo(FiresKey)
    }

    @Test fun no_se_apila_dos_veces_la_misma_pantalla_seguida() {
        val (nav, state) = navigator()
        nav.navigate(ChatKey(laura))
        nav.navigate(ChatKey(laura))
        assertThat(state.backStacks.getValue(InboxKey)).containsExactly(InboxKey, ChatKey(laura)).inOrder()
    }

    @Test fun abrir_la_ficha_de_la_orden_desde_el_chat_la_deja_arriba_del_chat() {
        val (nav, state) = navigator()
        nav.navigate(ChatKey(laura))
        nav.navigate(OrderSheetKey(OrderId.parse("order_01HX")!!))
        assertThat(state.visibleKeys().takeLast(2)).containsExactly(ChatKey(laura), OrderSheetKey(OrderId.parse("order_01HX")!!)).inOrder()
    }
}
