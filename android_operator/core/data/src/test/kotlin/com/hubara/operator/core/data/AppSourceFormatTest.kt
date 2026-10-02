package com.hubara.operator.core.data

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.repo.ChatView
import com.hubara.operator.core.data.screens.sources.chatJson
import com.hubara.operator.core.data.screens.sources.conversationJson
import com.hubara.operator.core.data.screens.sources.displayPhone
import com.hubara.operator.core.data.screens.sources.fireJson
import com.hubara.operator.core.data.screens.sources.inboxPreview
import com.hubara.operator.core.data.screens.sources.conversationSubtitle
import com.hubara.operator.core.data.screens.sources.templateJson
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.model.Template
import com.hubara.operator.core.model.TemplateVariable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import org.junit.Test

/**
 * Las fuentes del teléfono entregan los datos ya listos para mostrar (lo que antes hacían a mano las pantallas
 * nativas): el JSON de cada pantalla solo los enlaza. Estas reglas venían de InboxFilterTest y FireRoutingTest.
 */
class AppSourceFormatTest {
    private val laura = Conversation(
        SessionId.parse("wa_test_laura")!!, "570000000000", "INTERESADO", Route.BOT, 1_000, null, 3, null, customerName = "Laura Prueba",
        lastMessagePreview = "¿y qué aromas tienen?",
    )

    private fun JsonObject.str(key: String) = (this[key] as JsonPrimitive).content

    @Test fun la_fila_de_la_bandeja_habla_en_espanol_llano() {
        val row = conversationJson(laura, unseen = 2)
        assertThat(row.str("session_id")).isEqualTo("wa_test_laura")
        assertThat(row.str("title")).isEqualTo("Laura Prueba")
        assertThat(row.str("detail")).isEqualTo("+57 000 000 0000 · Bot · Interesado")
        assertThat(row.str("preview")).isEqualTo("¿y qué aromas tienen?")
        assertThat(row.str("unseen")).isEqualTo("2")
        assertThat(row.str("unseen_label")).isEqualTo("2 mensajes sin leer")
        assertThat(row.str("unread")).isEqualTo("true")
        assertThat(row.str("human")).isEqualTo("false")
        assertThat(row.str("has_order")).isEqualTo("false")
        assertThat(row.str("all")).isEqualTo("true")
        assertThat(row.str("time_ms")).isEqualTo("1000")

        val sinNombre = conversationJson(laura.copy(customerName = null), unseen = 1)
        assertThat(sinNombre.str("title")).isEqualTo("+57 000 000 0000")
        assertThat(sinNombre.str("detail")).isEqualTo("Bot · Interesado")
        assertThat(sinNombre.str("unseen_label")).isEqualTo("1 mensaje sin leer")
        assertThat(conversationJson(laura, unseen = 0).str("unseen_label")).isEqualTo("")
    }

    @Test fun sin_codigos_del_backend_a_la_vista() {
        val pedido41 = OrderRef(OrderId.parse("o41")!!, "41", PaymentState.CONFIRMED, 1)
        assertThat(conversationSubtitle(Route.BOT, "NO_ETIQUETADO", pedido41)).isEqualTo("Bot · Pendiente · Pedido #41")
        assertThat(conversationSubtitle(Route.BOT, "RECHAZO", null)).isEqualTo("Bot · Frío")
        assertThat(conversationSubtitle(Route.HUMAN, "HUMANO", null)).isEqualTo("Humano")
        assertThat(conversationSubtitle(Route.BOT, "ALGO_NUEVO", null)).isEqualTo("Bot")
        assertThat(displayPhone("")).isEqualTo("Cliente")
        assertThat(displayPhone("12345")).isEqualTo("12345")
        assertThat(inboxPreview("[datos de envío recibidos] receiver_name=Camilo Prueba; city=Bogo…")).isEqualTo("Datos de envío recibidos")
        assertThat(inboxPreview(null)).isNull()
        val conPedido = conversationJson(laura.copy(route = Route.HUMAN, orderRef = pedido41), unseen = 0)
        assertThat(conPedido.str("human")).isEqualTo("true")
        assertThat(conPedido.str("has_order")).isEqualTo("true")
    }

    private val sofia = SessionId.parse("wa_test_sofia")!!
    private fun fire(subject: FireSubject, action: String, severity: Severity = Severity.GRAVE, worse: Boolean = false) = Fire(
        FireId.parse("chat:wa_test_sofia")!!, subject, severity, FireKind.OTHER, worse, "Sofía pide un humano", "12 min", ActionRef(action), "rules", 0,
    )

    // Desde Incendios todo chat se abre «en vivo»; una orden abre su ficha si tiene id, y si no, su chat.
    @Test fun el_incendio_dice_a_donde_lleva_y_como_se_etiqueta() {
        val chat = fireJson(fire(FireSubject.Chat(sofia), "open_chat", worse = true))
        assertThat(chat.str("overline")).isEqualTo("CHAT · GRAVE · EMPEORA")
        assertThat(chat.str("opens")).isEqualTo("chat")
        assertThat(chat.str("session_id")).isEqualTo("wa_test_sofia")
        assertThat(chat.str("severity")).isEqualTo("grave")
        assertThat(chat.str("grave")).isEqualTo("true")
        assertThat(chat.str("chat")).isEqualTo("true")

        val order = OrderId.parse("order_01HX")!!
        val conOrden = fireJson(fire(FireSubject.Order(order, sofia), "open_order", Severity.HOY))
        assertThat(conOrden.str("overline")).isEqualTo("ORDEN · HOY")
        assertThat(conOrden.str("opens")).isEqualTo("order")
        assertThat(conOrden.str("order_id")).isEqualTo("order_01HX")
        assertThat(conOrden.str("order")).isEqualTo("true")
        assertThat(conOrden.str("grave")).isEqualTo("false")

        assertThat(fireJson(fire(FireSubject.Order(null, sofia), "open_order")).str("opens")).isEqualTo("chat")
        assertThat(fireJson(fire(FireSubject.Order(null, null), "open_order")).str("opens")).isEqualTo("")
    }

    @Test fun el_encabezado_del_chat() {
        fun view(name: String?, route: Route, ref: OrderRef?) =
            ChatView(sofia, "570000000000", name, route, ref, windowExpiresAtMs = 2_000, messages = emptyList(), pendingActions = emptyList())
        val bot = chatJson(view("Sofía Prueba", Route.BOT, null), nowMs = 1_000)
        assertThat(bot.str("title")).isEqualTo("Sofía Prueba")
        assertThat(bot.str("subtitle")).isEqualTo("+57 000 000 0000 · El bot atiende")
        assertThat(bot.str("human")).isEqualTo("false")
        assertThat(bot.str("window_open")).isEqualTo("true")
        assertThat(bot.str("order_id")).isEqualTo("")

        val humano = chatJson(view(null, Route.HUMAN, OrderRef(OrderId.parse("o41")!!, "41", PaymentState.PENDING, 1)), nowMs = 3_000)
        assertThat(humano.str("title")).isEqualTo("+57 000 000 0000")
        assertThat(humano.str("subtitle")).isEqualTo("Tú atiendes")
        assertThat(humano.str("window_open")).isEqualTo("false")
        assertThat(humano.str("order_id")).isEqualTo("o41")
        assertThat(humano.str("order_label")).isEqualTo("Pedido #41")
        val varios = chatJson(view(null, Route.HUMAN, OrderRef(OrderId.parse("o41")!!, "41", PaymentState.PENDING, 2)), nowMs = 0)
        assertThat(varios.str("order_label")).isEqualTo("Pedidos · 2")
    }

    @Test fun la_plantilla_trae_su_vista_previa_con_huecos_con_nombre() {
        val t = Template(
            "human_followup_utility_v1", "Hola {{1}}, te escribimos por {{2}}.",
            listOf(TemplateVariable("nombre", "Nombre del cliente", 60), TemplateVariable("tema", null, null)),
            isDefault = true, needsImage = false,
        )
        val json = templateJson(t)
        assertThat(json.str("label")).isEqualTo("Seguimiento del equipo (mensaje libre) · recomendada")
        assertThat(json.str("preview_body")).isEqualTo("Hola {{nombre}}, te escribimos por {{tema}}.")
        assertThat(json["fallback"]).isEqualTo(Json.parseToJsonElement("""{"nombre": "[Nombre del cliente]", "tema": "[tema]"}"""))
        assertThat(json["variable_names"]!!.jsonArray.map { (it as JsonPrimitive).content }).containsExactly("nombre", "tema").inOrder()
        assertThat(json["variables"]).isEqualTo(
            Json.parseToJsonElement("""[{"name": "nombre", "label": "Nombre del cliente", "max_length": 60}, {"name": "tema", "label": "tema", "max_length": null}]"""),
        )
        assertThat(json.str("needs_image")).isEqualTo("false")
    }
}
