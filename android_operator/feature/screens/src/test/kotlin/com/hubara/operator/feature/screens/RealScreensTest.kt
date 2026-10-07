package com.hubara.operator.feature.screens

import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.AppSource
import com.hubara.operator.core.data.screens.NativeActions
import com.hubara.operator.core.data.screens.NativeOutcome
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.navigation.LiveKey
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.sdui.parseScreen
import java.io.File
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Las pantallas REALES del repo (`android_operator/screens/`) con el ViewModel de verdad: tienen que comportarse como
 * las pantallas nativas que reemplazaron (estos casos venían de InboxSignOutTest, InboxPrivacyTest, InboxFilterTest,
 * FireRoutingTest, la ficha de Órdenes y la paleta del chat).
 */
@RunWith(AndroidJUnit4::class)
class RealScreensTest {
    @get:Rule val compose = createComposeRule()

    private val dir = File(requireNotNull(System.getProperty("screens.dir")))
    private val effects = mutableListOf<ScreenEffect>()
    private val natives = mutableListOf<Pair<String, JsonObject>>()
    private val sources = mutableMapOf<String, MutableStateFlow<JsonElement>>()
    private val data = FakeData()

    private fun json(raw: String) = Json.parseToJsonElement(raw)

    private val scope = kotlinx.coroutines.MainScope()

    @org.junit.After fun tearDown() = scope.cancel()

    /** La pantalla de prueba mide 320 px: se desliza hasta el elemento antes de tocarlo (si no, el toque cae afuera). */
    private fun node(text: String) = compose.onNodeWithTag(SCREEN_LIST_TAG).performScrollToNode(hasText(text)).let { compose.onNodeWithText(text) }

    private fun open(id: String, params: Map<String, String> = emptyMap()): ScreenViewModel {
        val docs = FakeDocs().apply { fresh[id] = File(dir, "$id.json").readText() }
        val apps = sources.mapValues { (_, flow) -> object : AppSource { override fun observe(params: Map<String, String>) = flow } }
        val vm = ScreenViewModel(id, params, docs, data, MemoryCache(), FakeChanges(), { 1_790_000_000_000L }, apps,
            NativeActions { name, args -> natives += name to args; NativeOutcome.Done })
        compose.setContent {
            OperatorTheme {
                val ui by vm.state.collectAsState()
                ServerScreen(ui, ScreenCallbacks(onAction = vm::onAction, onBind = vm::onBind, onConfirm = vm::onConfirm, onRefresh = vm::refresh))
            }
        }
        scope.launch { vm.effects.collect { effects += it } }
        compose.runOnIdle { }
        return vm
    }

    @Test fun la_bandeja_filtra_no_leidos_y_cerrar_sesion_pide_confirmacion() {
        sources["conversations"] = MutableStateFlow(json("""[
          {"session_id": "wa_000000000101", "title": "Laura Prueba", "name": "Laura Prueba", "detail": "000000000101 · Bot · Interesado",
           "preview": "¿y qué aromas tienen?", "unseen": 2, "unseen_label": "2 mensajes sin leer", "unread": true, "human": false, "has_order": false, "all": true, "time_ms": 1789999000000},
          {"session_id": "wa_000000000102", "title": "Sofía Prueba", "name": "Sofía Prueba", "detail": "000000000102 · Humano",
           "preview": "hola", "unseen": 0, "unseen_label": "", "unread": false, "human": true, "has_order": false, "all": true, "time_ms": 1789999000000}]"""))
        sources["app"] = MutableStateFlow(json("""{"privacy": true}"""))
        open("chats")
        compose.onNodeWithText("Laura Prueba").assertExists()
        compose.onNode(hasContentDescription("2 mensajes sin leer")).assertExists()
        compose.onNodeWithText("No leídos").performClick()
        compose.onNodeWithText("Sofía Prueba").assertDoesNotExist()
        compose.onNodeWithText("Humano").performClick()
        compose.onNodeWithText("Laura Prueba").assertDoesNotExist()
        compose.onNodeWithText("Sofía Prueba").assertExists()

        compose.onNode(hasContentDescription("Más opciones")).performClick()
        compose.onNodeWithText("Política de privacidad").performClick()
        assertThat(natives.map { it.first }).containsExactly("open_privacy")

        compose.onNode(hasContentDescription("Más opciones")).performClick()
        compose.onNodeWithText("Cerrar sesión").performClick()
        compose.onNodeWithText("Se borran de este teléfono", substring = true).assertExists()
        compose.onNodeWithText("Cancelar").performClick()
        assertThat(natives.map { it.first }).doesNotContain("sign_out")
        compose.onNode(hasContentDescription("Más opciones")).performClick()
        compose.onNodeWithText("Cerrar sesión").performClick()
        // El botón del diálogo (el menú ya se cerró).
        compose.onNodeWithText("Cerrar sesión").performClick()
        compose.runOnIdle { assertThat(natives.map { it.first }).contains("sign_out") }
    }

    @Test fun un_incendio_de_orden_abre_su_ficha_y_uno_de_chat_lo_abre_en_vivo() {
        sources["fires"] = MutableStateFlow(json("""[
          {"fire_id": "chat:wa_000000000102", "kind": "chat", "severity": "grave", "overline": "CHAT · GRAVE", "title": "Sofía pide un humano",
           "subtitle": "12 min", "session_id": "wa_000000000102", "order_id": null, "opens": "chat", "all": true, "grave": true, "chat": true, "order": false},
          {"fire_id": "order:order_41", "kind": "order", "severity": "grave", "overline": "ORDEN · GRAVE", "title": "Pedido #41 va retrasado",
           "subtitle": "4 días", "session_id": "wa_000000000104", "order_id": "order_41", "opens": "order", "all": true, "grave": true, "chat": false, "order": true}]"""))
        open("incendios")
        compose.onNodeWithText("Todo 2").assertExists()
        compose.onNodeWithText("Pedido #41 va retrasado").performClick()
        compose.onNodeWithText("Sofía pide un humano").performClick()
        compose.runOnIdle {
            assertThat(effects.filterIsInstance<ScreenEffect.Open>().map { it.key })
                .containsExactly(OrderSheetKey(OrderId.parse("order_41")!!), LiveKey(SessionId.parse("wa_000000000102")!!)).inOrder()
        }
        compose.onNodeWithText("Órdenes 1").performClick()
        compose.onNodeWithText("Sofía pide un humano").assertDoesNotExist()
    }

    private fun pedido(status: String) = """{"summary": {"id": "order_40", "display_id": "#40", "customer": "Juan Prueba", "status": "$status",
        "pay_status": "paid", "total_cop": 103940}, "items_detail": [{"title": "Dúo Zodiacal", "quantity": 1, "total_cop": 96040}],
        "shipping_address": {"first_name": "Juan", "last_name": "Prueba", "address_1": "Calle Falsa 1", "address_2": "Laureles", "city": "Medellín"},
        "shipping_cop": 7900, "payment_method_label": "Transferencia"}"""

    @Test fun la_ficha_del_pedido_ofrece_el_paso_que_sigue_segun_la_etapa() {
        data.ok("/api/orders/orders/order_40", pedido("preparing"))
        open("pedido", mapOf("order_id" to "order_40"))
        compose.onNodeWithText("Pedido #40").assertExists()
        compose.onNodeWithText("Dúo Zodiacal × 1").assertExists()
        node("DIRECCIÓN").assertExists()
        node("Laureles · Medellín").assertExists()
        node("Transferencia · pagado ✓").assertExists()
        compose.onNodeWithTag(SCREEN_LIST_TAG).performScrollToNode(hasContentDescription("Etapa: Preparando"))
        compose.onNode(hasContentDescription("Etapa: Preparando")).assertExists()
        compose.onNodeWithTag(SCREEN_LIST_TAG).performScrollToNode(hasText("hace falta la foto", substring = true))
        compose.onNodeWithText("Marcar listo").assertDoesNotExist()
    }

    @Test fun despachar_pide_la_guia_y_el_costo_antes_de_llamar() {
        data.ok("/api/orders/orders/order_40", pedido("ready"))
        data.ok("/api/orders/orders/order_40/stage", """{"success": true}""")
        open("pedido", mapOf("order_id" to "order_40"))
        node("Despachar").assertIsNotEnabled()
        node("Link de la guía").performTextInput("https://envia.co/g/1")
        node("Despachar").assertIsNotEnabled()
        node("Costo real del envío").performTextInput("12.000")
        node("Despachar").assertIsEnabled().performClick()
        compose.runOnIdle {
            val patch = data.calls.single { it.method == "PATCH" }
            assertThat(patch.path).isEqualTo("/api/orders/orders/order_40/stage")
            assertThat(patch.body).isEqualTo(json("""{"stage": "shipping", "tracking_url": "https://envia.co/g/1", "shipping_cost": 12000}"""))
        }
    }

    @Test fun en_camino_se_marca_entregado() {
        data.ok("/api/orders/orders/order_40", pedido("shipping"))
        open("pedido", mapOf("order_id" to "order_40"))
        node("Marcar entregado").performClick()
        compose.runOnIdle {
            assertThat(data.calls.single { it.method == "PATCH" }.body).isEqualTo(json("""{"stage": "delivered"}"""))
        }
    }

    @Test fun la_paleta_solo_ofrece_acciones_que_no_piden_un_producto() {
        val doc = parseScreen(File(dir, "acciones.json").readText()).doc!!
        val tools = Regex("\"tool\": \"(\\w+)\"").findAll(File(dir, "acciones.json").readText()).map { it.groupValues[1] }.toList()
        assertThat(tools).containsExactly(
            "present_products", "request_shipping_details", "send_shipping_rates", "present_order_confirmation", "send_payment_methods",
        )
        assertThat(doc.params).containsExactly("session")
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Medios de pago").performClick()
        compose.runOnIdle {
            assertThat(natives.single()).isEqualTo(
                "send_tool" to json("""{"session": "wa_000000000101", "tool": "send_payment_methods", "label": "Medios de pago"}"""),
            )
        }
    }

    @Test fun la_plantilla_recomendada_abre_lista_con_su_vista_previa() {
        sources["templates"] = MutableStateFlow(json("""[
          {"name": "human_followup_utility_v1", "title": "Seguimiento del equipo", "label": "Seguimiento del equipo · recomendada",
           "body": "Hola, {{1}}.", "preview_body": "Hola, {{tema}}.", "fallback": {"tema": "[Mensaje del operador]"}, "needs_image": false,
           "is_default": true, "variables": [{"name": "tema", "label": "Mensaje del operador", "max_length": 60}], "variable_names": ["tema"]},
          {"name": "order_ready_photo_utility_v1", "title": "Pedido listo (con foto)", "label": "Pedido listo (con foto)", "body": "x",
           "preview_body": "x", "fallback": {}, "needs_image": true, "is_default": false, "variables": [], "variable_names": []}]"""))
        open("plantillas", mapOf("session" to "wa_000000000105"))
        compose.onNodeWithText("Seguimiento del equipo · recomendada").assertExists()
        compose.onNodeWithText("Hola, [Mensaje del operador].").assertExists()
        node("Enviar plantilla").assertIsNotEnabled()
        node("Mensaje del operador").performTextInput("tu pedido de velas")
        node("Hola, tu pedido de velas.").assertExists()
        node("Enviar plantilla").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertThat(natives.single()).isEqualTo(
                "send_template" to json("""{"session": "wa_000000000105", "template": "human_followup_utility_v1", "values": {"tema": "tu pedido de velas"}}"""),
            )
        }
        compose.onNodeWithText("Elegir otra").assertExists()
    }
}
