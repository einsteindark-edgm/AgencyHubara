package com.hubara.operator.feature.inbox

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SeenCounts
import com.hubara.operator.core.model.SessionId
import org.junit.Test

class InboxFilterTest {
    private fun c(id: String, route: Route = Route.BOT, inbound: Int = 0, order: Boolean = false) = Conversation(
        SessionId.parse(id)!!, "", "", route, 0, null, inbound,
        if (order) OrderRef(OrderId.parse("o1")!!, "1", PaymentState.PENDING, 1) else null,
    )

    private fun ids(rows: List<InboxRow>) = rows.map { it.conversation.sessionId.raw }

    @Test fun filtros() {
        val all = listOf(c("wa_a", inbound = 4), c("wa_b", inbound = 2), c("wa_c", route = Route.HUMAN), c("wa_d", order = true))
        // wa_b escribió 2 mensajes desde que el operador lo abrió; wa_a ya estaba visto.
        val seen = SeenCounts(baseline = true, seen = mapOf("wa_a" to 4, "wa_c" to 0, "wa_d" to 0))
        val rows = inboxRows(all, seen)
        assertThat(InboxFilter.TODOS.apply(rows)).hasSize(4)
        assertThat(ids(InboxFilter.NO_LEIDOS.apply(rows))).containsExactly("wa_b")
        assertThat(rows.first { it.conversation.sessionId.raw == "wa_b" }.unseen).isEqualTo(2)
        assertThat(ids(InboxFilter.HUMANO.apply(rows))).containsExactly("wa_c")
        assertThat(ids(InboxFilter.CON_PEDIDO.apply(rows))).containsExactly("wa_d")
        assertThat(InboxFilter.NO_LEIDOS.label).isEqualTo("No leídos")
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

    // La bandeja solo mostraba el número: con el nombre de perfil de WhatsApp, arriba el nombre y debajo el número.
    @Test fun la_fila_muestra_el_nombre_arriba_y_el_numero_debajo_y_sin_nombre_el_numero() {
        val laura = Conversation(SessionId.parse("wa_test_laura")!!, "570000000000", "INTERESADO", Route.BOT, 0, null, 0, null,
            customerName = "Laura Prueba")
        assertThat(conversationTitle(laura)).isEqualTo("Laura Prueba")
        assertThat(conversationDetail(laura)).isEqualTo("+57 000 000 0000 · Bot · Interesado")

        val sinNombre = laura.copy(customerName = null)
        assertThat(conversationTitle(sinNombre)).isEqualTo("+57 000 000 0000")
        assertThat(conversationDetail(sinNombre)).isEqualTo("Bot · Interesado")
    }

    @Test fun el_telefono_se_muestra_agrupado_y_sin_prefijo_del_vault() {
        assertThat(displayPhone("570000000000")).isEqualTo("+57 000 000 0000")
        assertThat(displayPhone("")).isEqualTo("Cliente")
        assertThat(displayPhone("12345")).isEqualTo("12345")
    }

    // En el emulador la fila de Camilo mostraba el formulario crudo («[datos de envío recibidos] receiver_name=Ca…»).
    @Test fun la_vista_previa_del_formulario_de_envio_se_lee_en_palabras() {
        assertThat(inboxPreview("[datos de envío recibidos] receiver_name=Camilo Prueba; city=Bogo…")).isEqualTo("Datos de envío recibidos")
        assertThat(inboxPreview("¿y qué aromas tienen? quiero 2")).isEqualTo("¿y qué aromas tienen? quiero 2")
        assertThat(inboxPreview(null)).isNull()
    }
}

