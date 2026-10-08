package com.hubara.operator.feature.screens

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.ui.LocalNativeComponents
import com.hubara.operator.core.ui.NativeComponent
import com.hubara.operator.core.ui.NativeProps
import androidx.compose.ui.test.assertCountEquals
import com.hubara.operator.core.sdui.Action
import com.hubara.operator.core.sdui.Catalog
import com.hubara.operator.core.sdui.Scope
import com.hubara.operator.core.sdui.Template
import com.hubara.operator.core.sdui.parseScreen
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Cómo se ve una pantalla del servidor: el mismo JSON tiene que dar los mismos textos, filas y acciones. */
@RunWith(AndroidJUnit4::class)
class ServerScreenTest {
    @get:Rule val compose = createComposeRule()

    private val actions = mutableListOf<Pair<Action, Scope>>()
    private val origins = mutableListOf<String?>()
    private val binds = mutableListOf<Triple<String, JsonElement, Boolean>>()
    private var retried = 0
    private var confirmed: Boolean? = null

    private val callbacks = ScreenCallbacks(
        onAction = { a, s, o ->
            actions += a to s
            origins += o
        },
        onBind = { b, v, t -> binds += Triple(b, v, t) },
        onRetry = { retried++ },
        onConfirm = { confirmed = it },
    )

    private fun ready(raw: String, data: Map<String, String> = emptyMap(), form: Map<String, JsonElement> = emptyMap()): ScreenUi {
        val doc = requireNotNull(parseScreen(raw).doc)
        return ScreenUi(phase = ScreenPhase.READY, doc = doc, data = data.mapValues { Json.parseToJsonElement(it.value) }, state = doc.state, form = form)
    }

    /** setContent va una sola vez por test: después solo cambia el estado (como cuando el ViewModel emite). */
    private val current = mutableStateOf<ScreenUi?>(null)

    private fun show(ui: ScreenUi) {
        val first = current.value == null
        current.value = ui
        if (first) compose.setContent { OperatorTheme { current.value?.let { ServerScreen(it, callbacks, nowMs = { NOW }) } } }
        compose.waitForIdle()
    }

    @Test fun pinta_el_titulo_los_textos_y_una_fila_por_elemento() {
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS)))
        compose.onNodeWithText("Ventas").assertExists()
        compose.onNodeWithText("2 pedidos").assertExists()
        compose.onNodeWithText("#41 · Laura").assertExists()
        compose.onNodeWithText("#42 · Sofía").assertExists()
        compose.onNodeWithText("$45.000").assertExists()
        compose.onNodeWithText("Listo").assertExists()
    }

    @Test fun un_menu_de_renglones_fijos_y_los_valores_calculados() {
        show(
            ready(
                """{"schema": 1, "id": "mas", "data": {"pedidos": {"get": "/api/orders/orders"}},
                   "computed": {"listos": "{{pedidos.orders | where:'status':'ready'}}"},
                   "body": [{"type": "list", "children": [
                     {"type": "list_item", "title": "Ventas", "subtitle": "{{listos | count | plural:'listo':'listos'}}", "chevron": true,
                      "action": {"type": "navigate", "screen": "ventas"}},
                     {"type": "list_item", "title": "Campañas", "action": {"type": "navigate", "screen": "campanas"}}]}]}""",
                mapOf("pedidos" to PEDIDOS),
            ),
        )
        compose.onNodeWithText("1 listo").assertExists()
        compose.onNodeWithText("Campañas").performClick()
        assertThat((actions.single().first as Action.Navigate).screen).isEqualTo("campanas")
    }

    @Test fun lo_que_se_toca_adentro_de_una_seccion_o_tarjeta_tambien_responde() {
        show(
            ready(
                """{"schema": 1, "id": "x", "body": [
                     {"type": "section", "title": "Venta", "children": [
                       {"type": "list", "children": [{"type": "list_item", "title": "Tarifas", "action": [{"type": "message", "text": "a"}, {"type": "back"}]}]}]},
                     {"type": "card", "children": [{"type": "column", "children": [{"type": "button", "text": "Despachar", "action": {"type": "message", "text": "b"}}]}]}]}""",
            ),
        )
        compose.onNodeWithText("Tarifas").performClick()
        compose.onNodeWithText("Despachar").performClick()
        assertThat(actions.map { it.first::class.simpleName }).containsExactly("Sequence", "Message").inOrder()
    }

    @Test fun una_lista_vacia_muestra_su_estado_vacio() {
        show(ready(VENTAS, mapOf("pedidos" to """{"orders": []}""")))
        compose.onNodeWithText("Todavía no hay pedidos").assertExists()
    }

    @Test fun lo_que_no_es_visible_no_se_pinta() {
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS)))
        compose.onNodeWithText("Hay pedidos atrasados").assertDoesNotExist()
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS.replace("\"overdue\": false", "\"overdue\": true"))))
        compose.onNodeWithText("Hay pedidos atrasados").assertExists()
    }

    @Test fun tocar_una_fila_manda_su_accion_con_ese_elemento() {
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS)))
        compose.onNodeWithText("#42 · Sofía").performClick()
        val (action, scope) = actions.single()
        assertThat(action).isInstanceOf(Action.OpenOrder::class.java)
        assertThat((action as Action.OpenOrder).order.text(scope)).isEqualTo("order_02")
    }

    @Test fun chips_y_campos_guardan_lo_que_elige_o_escribe_el_operador() {
        show(ready(FORMULARIO))
        compose.onNodeWithText("Listos").performClick()
        compose.onNodeWithText("Link de la guía").performTextInput("https://envia.co/1")
        compose.onNodeWithText("Buscar").performTextInput("vela")
        assertThat(binds).containsExactly(
            Triple("state.etapa", JsonPrimitive("ready"), false),
            Triple("form.guia", JsonPrimitive("https://envia.co/1"), false),
            Triple("state.q", JsonPrimitive("vela"), true),
        ).inOrder()
    }

    @Test fun un_boton_deshabilitado_no_dispara() {
        show(ready(FORMULARIO))
        compose.onNodeWithText("Despachar").assertIsNotEnabled().performClick()
        assertThat(actions).isEmpty()
    }

    @Test fun la_confirmacion_pide_permiso_antes_de_llamar() {
        show(ready(FORMULARIO).copy(confirm = PendingConfirm("¿Despachar el pedido?", "Se le avisa al cliente.", "Despachar ya", "Cancelar")))
        compose.onNodeWithText("¿Despachar el pedido?").assertExists()
        compose.onNodeWithText("Se le avisa al cliente.").assertExists()
        compose.onNodeWithText("Despachar ya").performClick()
        assertThat(confirmed).isTrue()
    }

    @Test fun mientras_llegan_sus_datos_muestra_cargando_con_descripcion_para_talkback() {
        show(ready(VENTAS))  // sin datos todavía
        compose.mainClock.advanceTimeBy(1_000)
        compose.onNodeWithContentDescription("Cargando…").assertExists()
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS)))
        compose.onNodeWithContentDescription("Cargando…").assertDoesNotExist()
        compose.onNodeWithText("#41 · Laura").assertExists()
    }

    @Test fun un_boton_que_espera_al_backend_muestra_que_trabaja_y_los_demas_esperan() {
        show(ready(BOTONES))
        compose.onNodeWithText("Confirmar pago").performClick()
        val origin = origins.single()
        assertThat(origin).isNotNull()
        show(ready(BOTONES).copy(busy = true, working = origin))
        compose.onAllNodesWithContentDescription("Trabajando…").assertCountEquals(1)
        compose.onNodeWithText("Confirmar pago").assertIsNotEnabled()
        compose.onNodeWithText("Despachar").assertIsNotEnabled().performClick()
        assertThat(actions).hasSize(1)
        show(ready(BOTONES))
        compose.onAllNodesWithContentDescription("Trabajando…").assertCountEquals(0)
    }

    @Test fun una_accion_del_menu_que_espera_al_backend_se_nota_arriba() {
        // «Devolver al bot» va en el menú del chat: no tiene botón donde mostrar que trabaja.
        show(ready(BOTONES))
        compose.onNodeWithTag(BUSY_BAR_TAG).assertDoesNotExist()
        show(ready(BOTONES).copy(busy = true, working = "menu"))
        compose.onNodeWithTag(BUSY_BAR_TAG).assertExists()
    }

    @Test fun sin_pantalla_sin_datos_o_con_una_app_vieja_dice_que_hacer() {
        show(ScreenUi(phase = ScreenPhase.MISSING))
        compose.onNodeWithText("No se pudo abrir esta pantalla").assertExists()
        compose.onNodeWithText("Reintentar").performClick()
        assertThat(retried).isEqualTo(1)

        show(ScreenUi(phase = ScreenPhase.OUTDATED))
        compose.onNodeWithText("Actualiza la app").assertExists()

        show(ready(VENTAS).copy(failed = setOf("pedidos")))
        compose.onNodeWithText("No se pudieron cargar los datos").assertExists()
    }

    @Test fun un_componente_que_esta_app_no_conoce_no_rompe_la_pantalla() {
        show(ready("""{"schema": 1, "id": "x", "body": [{"type": "text", "text": "Antes"}, {"type": "holograma"}, {"type": "text", "text": "Después"}]}"""))
        compose.onNodeWithText("Antes").assertExists()
        compose.onNodeWithText("Después").assertExists()
    }

    @Test fun todos_los_componentes_del_catalogo_se_pintan() {
        val types = Json.parseToJsonElement(CATALOGO).jsonObject["body"]!!.jsonArray
            .flatMap { node -> listOf(node) + (node.jsonObject["children"]?.jsonArray.orEmpty()) }
            .flatMap { node -> listOf(node) + listOfNotNull(node.jsonObject["item"]) }
            .map { it.jsonObject["type"]!!.jsonPrimitive.content }.toSet()
        // Agregar un componente al catálogo obliga a mostrarlo aquí.
        assertThat(types).containsExactlyElementsIn(Catalog.components.keys)

        show(ready(CATALOGO, mapOf("pedidos" to PEDIDOS), form = mapOf("ok" to JsonPrimitive(true))))
        listOf("Título", "Columna", "Tarjeta", "Sección", "Etiqueta", "Fila", "Ventas", "TOTAL", "Aviso", "Vacío", "Botón", "Hoy", "Guía", "Activo")
            .forEach { compose.onNodeWithTag(SCREEN_LIST_TAG).performScrollToNode(hasText(it, substring = true)) }
    }

    @Test fun encabezado_con_avatar_subtitulo_chip_y_menu_mas_opciones() {
        val chat = """{"schema": 1, "id": "chat", "params": ["session"], "layout": "fill",
            "data": {"chat": {"app": "chat", "params": {"session": "{{params.session}}"}}},
            "title": "{{chat.title}}", "subtitle": "{{chat.subtitle}}", "avatar": "{{chat.title}}", "avatar_seed": "{{params.session}}",
            "topActions": [
              {"style": "chip", "icon": "orders", "label": "{{chat.order_label}}", "visible": "{{chat.order_id | present}}",
               "action": {"type": "open_order", "order": "{{chat.order_id}}"}},
              {"style": "menu", "icon": "bot", "label": "Devolver al bot", "visible": "{{chat.human}}",
               "action": {"type": "native", "name": "return_to_bot", "args": {"session": "{{params.session}}"}}}],
            "body": [{"type": "chat", "session": "{{params.session}}", "weight": 1,
                      "on_more": {"type": "navigate", "screen": "acciones", "params": {"session": "{{params.session}}"}, "sheet": true}}]}"""
        val doc = requireNotNull(parseScreen(chat).doc)
        val ui = ScreenUi(
            phase = ScreenPhase.READY, doc = doc, params = mapOf("session" to "wa_000000000104"),
            data = mapOf("chat" to Json.parseToJsonElement(
                """{"title": "Andrés Prueba", "subtitle": "000000000104 · Tú atiendes", "order_id": "order_41", "order_label": "Pedido #41", "human": true}""",
            )),
        )
        var island: NativeProps? = null
        val natives = mapOf<String, NativeComponent>("chat" to object : NativeComponent {
            @androidx.compose.runtime.Composable
            override fun Content(props: NativeProps, modifier: androidx.compose.ui.Modifier) {
                island = props
                androidx.compose.material3.Text("isla del chat ${props["session"]}", modifier)
            }
        })
        compose.setContent {
            OperatorTheme {
                androidx.compose.runtime.CompositionLocalProvider(LocalNativeComponents provides natives) {
                    ServerScreen(ui, callbacks, nowMs = { NOW })
                }
            }
        }
        compose.onNodeWithText("Andrés Prueba").assertExists()
        compose.onNodeWithText("000000000104 · Tú atiendes").assertExists()
        compose.onNodeWithText("isla del chat wa_000000000104").assertExists()
        compose.onNodeWithText("Pedido #41").performClick()
        assertThat((actions.last().first as Action.OpenOrder).order.text(actions.last().second)).isEqualTo("order_41")

        compose.onNode(androidx.compose.ui.test.hasContentDescription("Más opciones")).performClick()
        compose.onNodeWithText("Devolver al bot").performClick()
        assertThat((actions.last().first as Action.Native).name).isEqualTo("return_to_bot")

        // Las acciones del componente nativo son las del JSON.
        compose.runOnIdle { island!!.action("on_more")!!.invoke() }
        assertThat((actions.last().first as Action.Navigate).screen).isEqualTo("acciones")
    }

    @Test fun la_fila_de_la_bandeja_y_los_pasos_del_pedido() {
        show(
            ready(
                """{"schema": 1, "id": "x", "body": [
                     {"type": "list_item", "title": "Laura Prueba", "subtitle": "¿y qué aromas tienen?", "emphasis": true,
                      "badge": "2", "badge_label": "2 mensajes sin leer", "trailing_caption": "3:42 p. m.", "trailing_tone": "primary",
                      "caption": "+57 000 000 0000 · Bot · Interesado", "caption_icon": "bot", "caption_icon_tone": "bot"},
                     {"type": "list_item", "title": "Sin leídos", "badge": "0", "badge_label": ""},
                     {"type": "stepper", "label": "Etapa: Preparando", "current": "preparing",
                      "steps": [{"value": "new", "label": "Nuevo"}, {"value": "preparing", "label": "Preparando"}, {"value": "ready", "label": "Listo"}]}]}""",
            ),
        )
        compose.onNode(androidx.compose.ui.test.hasContentDescription("2 mensajes sin leer")).assertExists()
        compose.onNodeWithText("+57 000 000 0000 · Bot · Interesado").assertExists()
        compose.onAllNodes(androidx.compose.ui.test.hasContentDescription("mensajes sin leer", substring = true)).assertCountEquals(1)
        compose.onNode(androidx.compose.ui.test.hasContentDescription("Etapa: Preparando")).assertExists()
    }

    @Test fun todos_los_iconos_del_catalogo_estan_en_la_app() {
        assertThat(OperatorIcons.names).containsAtLeastElementsIn(Catalog.icons)
    }

    @Test fun la_accion_de_la_barra_superior_lleva_su_nombre_para_talkback() {
        show(ready(VENTAS, mapOf("pedidos" to PEDIDOS)))
        compose.onNodeWithText("Recargar").assertDoesNotExist() // es un ícono…
        compose.onNode(androidx.compose.ui.test.hasContentDescription("Recargar")).performClick() // …con descripción
        assertThat(actions.single().first).isEqualTo(Action.Refresh(emptyList()))
    }

    companion object {
        const val NOW = 1_790_000_000_000L

        val PEDIDOS = """{"orders": [
            {"id": "order_01", "display_id": "#41", "customer": "Laura", "status": "new", "total_cop": 45000, "overdue": false},
            {"id": "order_02", "display_id": "#42", "customer": "Sofía", "status": "ready", "total_cop": 30000, "overdue": false}
        ]}"""

        val VENTAS = """
        {
          "schema": 1, "id": "ventas", "title": "Ventas",
          "data": { "pedidos": { "get": "/api/orders/orders" } },
          "topActions": [ { "icon": "refresh", "label": "Recargar", "action": { "type": "refresh" } } ],
          "body": [
            { "type": "text", "text": "{{pedidos.orders | count}} pedidos", "style": "headline" },
            { "type": "notice", "tone": "danger", "text": "Hay pedidos atrasados",
              "visible": "{{pedidos.orders | where:'overdue' | count | gt:0}}" },
            { "type": "list", "items": "{{pedidos.orders}}", "as": "p", "key": "{{p.id}}",
              "item": { "type": "list_item", "title": "#{{p.display_id | replace:'#':''}} · {{p.customer}}",
                        "tag": "{{p.status | map:'new=Nuevo;ready=Listo'}}", "trailing": "{{p.total_cop | money}}",
                        "action": { "type": "open_order", "order": "{{p.id}}" } },
              "empty": { "type": "empty", "title": "Todavía no hay pedidos" } }
          ]
        }
        """.trimIndent()

        val BOTONES = """
        {
          "schema": 1, "id": "botones",
          "body": [
            { "type": "button", "text": "Confirmar pago", "icon": "payments",
              "action": { "type": "call", "method": "PATCH", "path": "/api/orders/orders/o1/confirm-payment" } },
            { "type": "button", "text": "Despachar", "style": "tonal",
              "action": { "type": "call", "method": "PATCH", "path": "/api/orders/orders/o1/stage" } }
          ]
        }
        """.trimIndent()

        val FORMULARIO = """
        {
          "schema": 1, "id": "formulario", "state": { "etapa": "new", "q": "" },
          "body": [
            { "type": "chips", "bind": "state.etapa", "options": [ { "value": "new", "label": "Nuevos" }, { "value": "ready", "label": "Listos" } ] },
            { "type": "text_field", "bind": "form.guia", "label": "Link de la guía", "keyboard": "url" },
            { "type": "text_field", "bind": "state.q", "label": "Buscar" },
            { "type": "button", "text": "Despachar", "enabled": "{{form.guia | present}}",
              "action": { "type": "message", "text": "ok" } }
          ]
        }
        """.trimIndent()

        /** Uno de cada componente del catálogo. */
        val CATALOGO = """
        {
          "schema": 1, "id": "catalogo", "state": { "rango": "today" },
          "data": { "pedidos": { "get": "/api/orders/orders" } },
          "body": [
            { "type": "text", "text": "Título", "style": "title", "emphasis": true },
            { "type": "column", "children": [ { "type": "text", "text": "Columna" }, { "type": "spacer", "size": 8 }, { "type": "divider" } ] },
            { "type": "row", "children": [ { "type": "icon", "name": "star" }, { "type": "avatar", "name": "Laura Prueba" },
                                            { "type": "tag", "text": "Etiqueta", "tone": "success", "weight": 1 } ] },
            { "type": "card", "children": [ { "type": "text", "text": "Tarjeta" },
                                             { "type": "image", "url": "https://example.com/vela.jpg", "description": "Vela" } ] },
            { "type": "section", "title": "Sección", "action_label": "Ver todo", "action": { "type": "back" },
              "children": [ { "type": "list", "items": "{{pedidos.orders}}", "style": "cards",
                              "item": { "type": "list_item", "title": "Fila {{item.customer}}" } } ] },
            { "type": "grid", "columns": 2, "children": [ { "type": "stat", "label": "Ventas", "value": "{{pedidos.orders | sum:'total_cop' | money}}" },
                                                          { "type": "key_value", "label": "TOTAL", "value": "$75.000" } ] },
            { "type": "progress", "value": "0.5", "label": "Meta" },
            { "type": "stepper", "current": "b", "steps": [ { "value": "a", "label": "Uno" }, { "value": "b", "label": "Dos" } ] },
            { "type": "notifications_banner" },
            { "type": "chat", "session": "wa_000000000101" },
            { "type": "notice", "text": "Aviso", "tone": "info" },
            { "type": "empty", "title": "Vacío", "icon": "search" },
            { "type": "button", "text": "Botón", "style": "tonal", "icon": "add", "action": { "type": "back" } },
            { "type": "chips", "bind": "state.rango", "style": "segmented",
              "options": [ { "value": "today", "label": "Hoy" }, { "value": "7d", "label": "7 días" } ] },
            { "type": "text_field", "bind": "form.g", "label": "Guía" },
            { "type": "switch", "bind": "form.ok", "label": "Activo" }
          ]
        }
        """.trimIndent()
    }
}
