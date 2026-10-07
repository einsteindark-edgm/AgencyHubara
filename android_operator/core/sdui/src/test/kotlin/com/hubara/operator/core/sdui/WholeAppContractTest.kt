package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Test

/**
 * Lo que hace falta para que TODA la app sea pantallas del servidor: datos que viven en el teléfono (la bandeja en Room,
 * lo no leído), acciones nativas (outbox, tomar la conversación, cerrar sesión), confirmación y condiciones genéricas,
 * el encabezado del chat, una pantalla que se llena (el chat) y las pestañas completas en `app.json`.
 */
class WholeAppContractTest {

    private val known = mapOf(
        "chat" to listOf("session"), "pedido" to listOf("order_id"), "chats" to emptyList(), "incendios" to emptyList(),
        "acciones" to listOf("session"), "plantillas" to listOf("session"),
    )

    private fun problems(raw: String): List<String> {
        val parsed = parseScreen(raw)
        val doc = parsed.doc ?: return parsed.problems
        return parsed.problems + validateScreen(doc, known)
    }

    @Test fun `una fuente puede ser del telefono y no del backend`() {
        val doc = parseScreen(
            """{"schema": 1, "id": "chat", "params": ["session"], "data": {
                 "chats": {"app": "conversations"},
                 "chat": {"app": "chat", "params": {"session": "{{params.session}}"}}},
               "body": []}""",
        ).doc!!
        val chats = doc.data.getValue("chats")
        assertThat(chats.app).isEqualTo("conversations")
        val chat = doc.data.getValue("chat")
        val request = chat.appRequest(scopeOf("params" to json("""{"session": "wa_000000000101"}""")), TEST_ENV)!!
        assertThat(request).isEqualTo(AppRequest("chat", mapOf("session" to "wa_000000000101")))
        assertThat(chats.resolve(Scope.Empty, TEST_ENV)).isNull()
        assertThat(problems(VALID_CHAT)).isEmpty()
    }

    @Test fun `fuentes del telefono que no existen o mal declaradas`() {
        assertThat(problems("""{"schema": 1, "id": "x", "data": {"a": {"app": "inventada"}}, "body": []}""").single()).contains("inventada")
        assertThat(problems("""{"schema": 1, "id": "x", "data": {"a": {"app": "conversations", "get": "/api/x"}}, "body": []}""").single())
            .contains("«get» o «app»")
        assertThat(problems("""{"schema": 1, "id": "x", "data": {"a": {"app": "chat"}}, "body": []}""").single()).contains("session")
    }

    @Test fun `el estado de cada fuente se lee en las plantillas`() {
        val ok = """{"schema": 1, "id": "x", "data": {"chats": {"app": "conversations"}},
                     "body": [{"type": "notice", "text": "Sin conexión", "visible": "{{status.chats.failed}}"}]}"""
        assertThat(problems(ok)).isEmpty()
        val scope = screenScope(emptyMap(), emptyMap(), emptyMap(), emptyMap(), NOW, status = json("""{"chats": {"failed": true}}"""))
        assertThat(Template.parse("{{status.chats.failed}}").evaluate(scope)).isEqualTo(JsonPrimitive(true))
    }

    @Test fun `acciones nativas, confirmacion generica y condiciones`() {
        val doc = parseScreen(
            """{"schema": 1, "id": "x", "body": [
                 {"type": "button", "text": "a", "action": {"type": "native", "name": "send_tool",
                   "args": {"session": "{{params.session}}", "tool": "present_products", "label": "Enviar productos"}}},
                 {"type": "button", "text": "b", "action": {"type": "confirm", "title": "¿Cerrar sesión?", "body": "Se borran los chats.",
                   "accept": "Cerrar sesión", "then": {"type": "native", "name": "sign_out"}}},
                 {"type": "button", "text": "c", "action": {"type": "if", "condition": "{{params.kind | eq:'order'}}",
                   "then": {"type": "open_order", "order": "{{params.id}}"}, "else": {"type": "open_chat", "session": "{{params.id}}", "live": true}}}
               ]}""",
        ).doc!!
        val native = doc.body[0].action as Action.Native
        assertThat(native.name).isEqualTo("send_tool")
        val ask = doc.body[1].action as Action.AskFirst
        assertThat(ask.confirm.accept).isEqualTo("Cerrar sesión")
        assertThat(ask.then).isEqualTo(Action.Native("sign_out", null))
        val choice = doc.body[2].action as Action.If
        assertThat(choice.condition.raw).isEqualTo("{{params.kind | eq:'order'}}")
        assertThat((choice.otherwise as Action.OpenChat).live).isTrue()
    }

    @Test fun `una accion nativa que no existe o sin sus argumentos`() {
        fun screen(action: String) = """{"schema": 1, "id": "x", "body": [{"type": "button", "text": "a", "action": $action}]}"""
        assertThat(problems(screen("""{"type": "native", "name": "borrar_todo"}""")).single()).contains("borrar_todo")
        assertThat(problems(screen("""{"type": "native", "name": "send_tool", "args": {"session": "wa_1"}}"""))).hasSize(2)
        assertThat(problems(screen("""{"type": "confirm", "title": "¿Seguro?"}""")).single()).contains("then")
        assertThat(problems(screen("""{"type": "if", "condition": "{{nada}}", "then": {"type": "back"}}""")).single()).contains("«nada»")
    }

    @Test fun `encabezado con avatar y subtitulo, acciones como chip o menu, y pantalla que se llena`() {
        val doc = parseScreen(VALID_CHAT).doc!!
        assertThat(doc.layout).isEqualTo("fill")
        assertThat(doc.subtitle?.raw).isEqualTo("{{chat.subtitle}}")
        assertThat(doc.avatar?.raw).isEqualTo("{{chat.title}}")
        assertThat(doc.avatarSeed?.raw).isEqualTo("{{params.session}}")
        val (chip, menu) = doc.topActions
        assertThat(chip.style).isEqualTo("chip")
        assertThat(chip.label.raw).isEqualTo("{{chat.order_label}}")
        assertThat(chip.visible.single().raw).isEqualTo("{{chat.order_id | present}}")
        assertThat(menu.style).isEqualTo("menu")
        val island = doc.body.single()
        assertThat(island.type).isEqualTo("chat")
        assertThat(island.actionProp("on_more")).isEqualTo(Action.Navigate("acciones", mapOf("session" to Template.parse("{{params.session}}")), sheet = true))
    }

    @Test fun `estilos de accion de la barra y layout validos`() {
        assertThat(problems("""{"schema": 1, "id": "x", "layout": "grilla", "body": []}""").single()).contains("grilla")
        val bad = """{"schema": 1, "id": "x", "topActions": [{"icon": "refresh", "label": "a", "style": "boton", "action": {"type": "back"}}], "body": []}"""
        assertThat(problems(bad).single()).contains("boton")
    }

    @Test fun `componentes nuevos de la app`() {
        val body = """[
          {"type": "notifications_banner"},
          {"type": "list_item", "title": "Laura", "subtitle": "hola", "badge": "2", "badge_label": "2 mensajes sin leer", "emphasis": true,
           "trailing_caption": "3:42 p. m.", "trailing_tone": "primary", "overline": "CHAT · GRAVE", "overline_tone": "danger",
           "caption": "Bot · Interesado", "caption_icon": "bot", "caption_icon_tone": "bot"},
          {"type": "stepper", "label": "Etapa: Preparando", "current": "preparing",
           "steps": [{"value": "new", "label": "Nuevo"}, {"value": "preparing", "label": "Preparando"}]},
          {"type": "text_field", "bind": "form.{{params.var}}", "label": "x", "max_length": 60}
        ]"""
        assertThat(problems("""{"schema": 1, "id": "x", "body": $body}""")).isEmpty()
        assertThat(problems("""{"schema": 1, "id": "x", "body": [{"type": "text_field", "bind": "otro.{{params.v}}", "label": "x"}]}""").single())
            .contains("form.")
        assertThat(Catalog.components.getValue("chat").native).isTrue()
        assertThat(problems("""{"schema": 1, "id": "x", "body": [{"type": "chat"}]}""").single()).contains("session")
    }

    @Test fun `las pestanas completas salen del manifiesto`() {
        val manifest = parseAppManifest(
            """{"schema": 1, "tabs": [
                 {"screen": "chats", "label": "Chats", "icon": "chat", "icon_selected": "chat_filled"},
                 {"screen": "incendios", "label": "Incendios", "icon": "fire", "icon_selected": "fire_filled", "badge": "{{radar | count}}"}]}""",
        ).manifest!!
        assertThat(manifest.tabs[1]).isEqualTo(TabSpec("incendios", "Incendios", "fire", iconSelected = "fire_filled", badge = Template.parse("{{radar | count}}")))
        assertThat(validateAppManifest(manifest, known)).isEmpty()

        val one = parseAppManifest("""{"schema": 1, "tabs": [{"screen": "chats", "label": "Chats", "icon": "chat"}]}""").manifest!!
        assertThat(validateAppManifest(one, known).single()).contains("2")
        val badBadge = parseAppManifest(
            """{"schema": 1, "tabs": [{"screen": "chats", "label": "Chats", "icon": "chat", "badge": "{{inventada | count}}"},
                 {"screen": "incendios", "label": "Incendios", "icon": "fire"}]}""",
        ).manifest!!
        assertThat(validateAppManifest(badBadge, known).single()).contains("inventada")
    }

    companion object {
        val VALID_CHAT = """
        {
          "schema": 1, "id": "chat", "params": ["session"], "layout": "fill",
          "data": { "chat": { "app": "chat", "params": { "session": "{{params.session}}" } } },
          "title": "{{chat.title}}", "subtitle": "{{chat.subtitle}}", "avatar": "{{chat.title}}", "avatar_seed": "{{params.session}}",
          "topActions": [
            { "style": "chip", "icon": "orders", "label": "{{chat.order_label}}", "visible": "{{chat.order_id | present}}",
              "action": { "type": "open_order", "order": "{{chat.order_id}}" } },
            { "style": "menu", "icon": "bot", "label": "Devolver al bot", "visible": "{{chat.human}}",
              "action": { "type": "native", "name": "return_to_bot", "args": { "session": "{{params.session}}" } } }
          ],
          "body": [
            { "type": "chat", "session": "{{params.session}}", "weight": 1,
              "on_more": { "type": "navigate", "screen": "acciones", "params": { "session": "{{params.session}}" }, "sheet": true },
              "on_reactivate": { "type": "navigate", "screen": "plantillas", "params": { "session": "{{params.session}}" }, "sheet": true } }
          ]
        }
        """.trimIndent()
    }
}
