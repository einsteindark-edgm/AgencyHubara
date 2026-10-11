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
import androidx.compose.ui.test.performTextReplacement
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

    /** Lo que devuelve `GET /api/chats/catalog`: Luz Serena no tiene colores para elegir. */
    private val catalogo = """{"products": [
      {"handle": "duo-zodiacal", "title": "Dúo Zodiacal", "price_cop": 89900, "thumbnail_url": null,
       "aromas": ["Lavanda", "Vainilla"], "colors": ["Azul", "Rosa"], "designs": []},
      {"handle": "luz-serena", "title": "Luz Serena", "price_cop": 29000, "thumbnail_url": null,
       "aromas": ["Lavanda", "Canela"], "colors": [], "designs": []}]}"""

    @Test fun la_paleta_manda_de_una_lo_que_no_pide_un_producto() {
        val doc = parseScreen(File(dir, "acciones.json").readText()).doc!!
        assertThat(doc.params).containsExactly("session")
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Medios de pago").performClick()
        compose.runOnIdle {
            assertThat(natives.single()).isEqualTo(
                "send_tool" to json("""{"session": "wa_000000000101", "tool": "send_payment_methods", "label": "Medios de pago"}"""),
            )
        }
    }

    /**
     * Caso 2026-10-09: el cliente pedía colores y la app no tenía cómo mandarlos (con el bot apagado el pedido no tiene
     * producto y la burbuja no sale). «Enviar colores» lista solo los productos que tienen colores para elegir.
     */
    @Test fun enviar_colores_pide_el_producto_y_solo_lista_los_que_tienen_colores() {
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Enviar colores").performClick()
        compose.onNodeWithText("Luz Serena").assertDoesNotExist()
        node("Dúo Zodiacal").performClick()
        compose.runOnIdle {
            assertThat(natives.single()).isEqualTo(
                "send_tool" to json("""{"session": "wa_000000000101", "tool": "present_variant_picker",
                  "label": "Enviar colores · Dúo Zodiacal", "args": {"product": "duo-zodiacal", "attribute": "color"}}"""),
            )
            assertThat(effects).contains(ScreenEffect.Back)
        }
    }

    @Test fun enviar_aromas_lista_los_productos_con_aromas() {
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Enviar aromas").performClick()
        node("Luz Serena").performClick()
        compose.runOnIdle {
            assertThat(natives.single().second["args"]).isEqualTo(json("""{"product": "luz-serena", "attribute": "aroma"}"""))
        }
    }

    /** «Pedir datos de envío» con el pedido vacío daba «faltan datos para esta acción»: ahora se elige qué y cuántos. */
    @Test fun pedir_datos_de_envio_usa_el_pedido_o_el_producto_y_la_cantidad_que_elige_el_operador() {
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Pedir datos de envío").performClick()
        node("Con los productos del pedido").performClick()
        compose.runOnIdle {
            assertThat(natives.last()).isEqualTo(
                "send_tool" to json("""{"session": "wa_000000000101", "tool": "request_shipping_details", "label": "Pedir datos de envío"}"""),
            )
        }
        node("2").performClick()
        node("Luz Serena").performClick()
        compose.runOnIdle {
            assertThat(natives.last()).isEqualTo(
                "send_tool" to json("""{"session": "wa_000000000101", "tool": "request_shipping_details",
                  "label": "Pedir datos de envío · 2× Luz Serena", "args": {"product": "luz-serena", "quantity": "2"}}"""),
            )
        }
    }

    @Test fun volver_a_las_acciones_deja_el_menu_como_estaba() {
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Enviar colores").performClick()
        node("Volver a las acciones").performClick()
        node("Medios de pago").assertExists()
        compose.onNodeWithText("Dúo Zodiacal").assertDoesNotExist()
    }

    /** Lo que devuelve `GET /api/chats/order-intake/{sesión}/form`: el pedido que leyó de la conversación. */
    private fun formulario(ready: Boolean = true) = """{"session_key": "wa_000000000101",
      "items": [{"handle": "duo-zodiacal", "title": "Dúo Zodiacal", "variant_label": null, "quantity": 2,
                 "unit_price_cop": 89900, "line_total_cop": 179800, "color": "Azul", "aroma": null}],
      "shipping": {"city": "Bogotá", "neighborhood": "Chapinero", "address": ${if (ready) "\"Cl 1 # 2-3\"" else "null"},
                   "phone": "3000000000", "receiver_name": "Ana", "national_id": null},
      "payment_method": "transfer", "payment_label": "Pago anticipado (transferencia o Nequi)",
      "subtotal_cop": 179800, "shipping_cop": 7900, "discount_cop": 0, "total_cop": 187700,
      "order_items": [{"handle": "duo-zodiacal", "quantity": 2, "color": "Azul"}],
      "ready": $ready, "missing_text": "${if (ready) "" else "Falta la dirección. Complétalo antes de crear el pedido."}",
      "warnings": [], "already_registered_order_id": null, "degraded": false}"""

    /** El humano cerró la venta: «Crear pedido» registra el pedido con lo que dijo el cliente, como en el dashboard. */
    @Test fun crear_pedido_registra_lo_que_se_ve_con_lo_que_el_operador_corrigio() {
        data.ok("/api/chats/order-intake/wa_000000000101/form", formulario())
        val vm = open("crear_pedido", mapOf("session" to "wa_000000000101"))
        node("2× Dúo Zodiacal").assertExists()
        node("Pago anticipado (transferencia o Nequi)").assertExists()
        node("Total").assertExists()
        node("Quién recibe").performTextReplacement("Ana Pérez")
        node("Crear pedido").performClick()
        compose.runOnIdle { vm.onConfirm(accepted = true) }
        compose.runOnIdle {
            val call = data.calls.single { it.method == "POST" }
            assertThat(call.path).isEqualTo("/api/chats/session-actions/wa_000000000101/order/app")
            assertThat(call.body).isEqualTo(json("""{
              "items": [{"handle": "duo-zodiacal", "quantity": 2, "color": "Azul"}],
              "shipping": {"city": "Bogotá", "neighborhood": "Chapinero", "address": "Cl 1 # 2-3", "phone": "3000000000",
                           "receiver_name": "Ana Pérez", "national_id": ""},
              "payment_method": "transfer", "send_payment_instructions": true, "expected_discount_cop": 0}"""))
            assertThat(effects).contains(ScreenEffect.Back)
        }
    }

    @Test fun crear_pedido_dice_que_falta_y_no_deja_crear_hasta_completarlo() {
        data.ok("/api/chats/order-intake/wa_000000000101/form", formulario(ready = false))
        open("crear_pedido", mapOf("session" to "wa_000000000101"))
        node("Falta la dirección. Complétalo antes de crear el pedido.").assertExists()
        node("Crear pedido").assertIsNotEnabled()
        node("Dirección").performTextInput("Cl 1 # 2-3")
        node("Crear pedido").assertIsEnabled()
    }

    @Test fun la_paleta_abre_crear_pedido() {
        data.ok("/api/chats/catalog", catalogo)
        open("acciones", mapOf("session" to "wa_000000000101"))
        node("Crear pedido").performClick()
        compose.runOnIdle {
            val open = effects.filterIsInstance<ScreenEffect.Open>().single().key
            assertThat(open).isEqualTo(com.hubara.operator.core.navigation.ScreenSheetKey("crear_pedido", mapOf("session" to "wa_000000000101")))
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
