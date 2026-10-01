package com.hubara.operator.feature.fires

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.navigation.ChatKey
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.OrderSheetKey
import org.junit.Test

class FireRoutingTest {
    private val sofia = SessionId.parse("wa_test_sofia")!!
    private fun fire(subject: FireSubject, action: String, severity: Severity = Severity.GRAVE) = Fire(
        FireId.parse("chat:wa_test_sofia")!!, subject, severity, FireKind.OTHER, false, "t", "", ActionRef(action), "rules", 0,
    )

    // Desde Incendios todo chat se abre como «en vivo» (LiveKey), que es de la pila de Incendios: ChatKey es de la
    // pila de Chats. Antes el mismo chat podía quedar abierto en las dos pilas y, en tablet, mezclarse con otra pestaña.
    @Test fun la_accion_principal_decide_a_donde_va() {
        assertThat(destinationFor(fire(FireSubject.Chat(sofia), "open_chat"))).isEqualTo(LiveKey(sofia))
        assertThat(destinationFor(fire(FireSubject.Chat(sofia), "open_live"))).isEqualTo(LiveKey(sofia))
        val order = OrderId.parse("order_01HX")!!
        assertThat(destinationFor(fire(FireSubject.Order(order, sofia), "open_order"))).isEqualTo(OrderSheetKey(order))
    }

    @Test fun una_orden_sin_id_cae_en_su_chat_y_una_accion_desconocida_tambien() {
        assertThat(destinationFor(fire(FireSubject.Order(null, sofia), "open_order"))).isEqualTo(LiveKey(sofia))
        assertThat(destinationFor(fire(FireSubject.Chat(sofia), "accion_nueva"))).isEqualTo(LiveKey(sofia))
        assertThat(destinationFor(fire(FireSubject.Order(null, null), "open_order"))).isNull()
    }

    @Test fun filtros() {
        val g = fire(FireSubject.Chat(sofia), "open_chat", Severity.GRAVE)
        val o = fire(FireSubject.Order(OrderId.parse("o1"), null), "open_order", Severity.HOY)
        assertThat(FireFilter.GRAVES.apply(listOf(g, o))).containsExactly(g)
        assertThat(FireFilter.CHATS.apply(listOf(g, o))).containsExactly(g)
        assertThat(FireFilter.ORDENES.apply(listOf(g, o))).containsExactly(o)
        assertThat(FireFilter.TODO.apply(listOf(g, o))).hasSize(2)
    }
}
