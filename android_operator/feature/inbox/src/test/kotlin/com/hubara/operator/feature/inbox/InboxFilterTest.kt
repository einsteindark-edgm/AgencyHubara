package com.hubara.operator.feature.inbox

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import org.junit.Test

class InboxFilterTest {
    private fun c(id: String, route: Route = Route.BOT, unanswered: Int = 0, order: Boolean = false) = Conversation(
        SessionId.parse(id)!!, "", "", route, 0, null, unanswered,
        if (order) OrderRef(OrderId.parse("o1")!!, "1", PaymentState.PENDING, 1) else null,
    )

    private val all = listOf(c("wa_a"), c("wa_b", unanswered = 2), c("wa_c", route = Route.HUMAN), c("wa_d", order = true))

    @Test fun filtros() {
        assertThat(InboxFilter.TODOS.apply(all)).hasSize(4)
        assertThat(InboxFilter.SIN_RESPONDER.apply(all).map { it.sessionId.raw }).containsExactly("wa_b")
        assertThat(InboxFilter.HUMANO.apply(all).map { it.sessionId.raw }).containsExactly("wa_c")
        assertThat(InboxFilter.CON_PEDIDO.apply(all).map { it.sessionId.raw }).containsExactly("wa_d")
    }

    // Mismo vocabulario que la bandeja del dashboard web: nada de códigos del backend a la vista.
    @Test fun la_fila_habla_en_espanol_llano_sin_codigos_del_backend() {
        val pedido41 = OrderRef(OrderId.parse("o41")!!, "41", PaymentState.CONFIRMED, 1)
        assertThat(conversationSubtitle(Route.BOT, "INTERESADO", null)).isEqualTo("Bot · Interesado")
        assertThat(conversationSubtitle(Route.BOT, "NO_ETIQUETADO", pedido41)).isEqualTo("Bot · Pendiente · Pedido #41")
        assertThat(conversationSubtitle(Route.BOT, "COMPRA_EXITOSA", null)).isEqualTo("Bot · Cliente")
        assertThat(conversationSubtitle(Route.BOT, "RECHAZO", null)).isEqualTo("Bot · Frío")
        assertThat(conversationSubtitle(Route.BOT, "SIN_RESPUESTA", null)).isEqualTo("Bot · Sin respuesta")
        // La ruta ya dice «Humano»: no se repite.
        assertThat(conversationSubtitle(Route.HUMAN, "HUMANO", null)).isEqualTo("Humano")
        // Un código que la app no conoce no se muestra crudo.
        assertThat(conversationSubtitle(Route.BOT, "ALGO_NUEVO", null)).isEqualTo("Bot")
    }

    @Test fun el_telefono_se_muestra_agrupado_y_sin_prefijo_del_vault() {
        assertThat(displayPhone("570000000000")).isEqualTo("+57 000 000 0000")
        assertThat(displayPhone("")).isEqualTo("Cliente")
        assertThat(displayPhone("12345")).isEqualTo("12345")
    }
}
