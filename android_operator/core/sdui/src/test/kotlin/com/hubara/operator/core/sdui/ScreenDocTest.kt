package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Test

class ScreenDocTest {

    @Test fun `lee una pantalla completa`() {
        val doc = requireNotNull(parseScreen(CAMPANAS).doc)
        assertThat(doc.id).isEqualTo("campanas")
        assertThat(doc.title?.raw).isEqualTo("Campañas")
        assertThat(doc.requires).isEqualTo(1)
        assertThat(doc.state).containsEntry("filtro", JsonPrimitive("todas"))

        val source = doc.data.getValue("campanas")
        assertThat(source.method).isEqualTo("GET")
        assertThat(source.path.raw).isEqualTo("/api/marketing/campaigns")
        assertThat(source.query.keys).containsExactly("status")
        assertThat(source.refreshOn).containsExactly("marketing")
        assertThat(source.every).isEqualTo(60)
        assertThat(source.optional).isFalse()

        assertThat(doc.body.map { it.type }).containsExactly("chips", "section").inOrder()
        val list = doc.body[1].children.single()
        assertThat(list.type).isEqualTo("list")
        assertThat(list.alias).isEqualTo("c")
        assertThat(list.item?.type).isEqualTo("list_item")
        assertThat(list.empty?.type).isEqualTo("empty")
        assertThat(list.template("items")?.raw).isEqualTo("{{campanas.campaigns}}")

        val row = list.item!!
        assertThat(row.visible.map { it.raw }).containsExactly("{{c.name | present}}")
        val nav = row.action as Action.Navigate
        assertThat(nav.screen).isEqualTo("campana")
        assertThat(nav.params.mapValues { it.value.raw }).containsExactly("campaign_id", "{{c.id}}")
        assertThat(nav.sheet).isFalse()

        assertThat(doc.topActions.single().icon).isEqualTo("refresh")
        assertThat(doc.topActions.single().action).isEqualTo(Action.Refresh(emptyList()))
        assertThat(doc.fab?.label).isEqualTo("Nueva campaña")
    }

    @Test fun `las claves que empiezan con guion bajo o pesos son notas y se ignoran`() {
        val result = parseScreen(
            """{"${'$'}schema": "./screen.schema.json", "_nota": "pantalla de prueba", "schema": 1, "id": "x",
               "body": [{"type": "text", "text": "Hola", "_por_que": "saludo"}]}""",
        )
        assertThat(result.problems).isEmpty()
        assertThat(result.doc!!.body.single().props.keys).containsExactly("text")
    }

    @Test fun `sin JSON, sin id o sin body no hay pantalla`() {
        assertThat(parseScreen("<html>no</html>").doc).isNull()
        assertThat(parseScreen("""{"schema": 1, "body": []}""").doc).isNull()
        assertThat(parseScreen("""{"schema": 1, "id": "x"}""").doc).isNull()
        assertThat(parseScreen("""{"schema": 1, "id": "x"}""").problems.single()).contains("body")
    }

    @Test fun `todas las acciones`() {
        val doc = requireNotNull(parseScreen(ACCIONES).doc)
        val actions = doc.body.map { it.action }
        assertThat(actions[0]).isInstanceOf(Action.OpenChat::class.java)
        assertThat(actions[1]).isInstanceOf(Action.OpenOrder::class.java)
        assertThat(actions[2]).isInstanceOf(Action.OpenUrl::class.java)
        assertThat(actions[3]).isEqualTo(Action.Back)
        assertThat((actions[4] as Action.Refresh).sources).containsExactly("pedidos")
        assertThat((actions[5] as Action.SetState).values.keys).containsExactly("filtro")
        val call = actions[6] as Action.Call
        assertThat(call.method).isEqualTo("PATCH")
        assertThat(call.path.raw).isEqualTo("/api/orders/orders/{{params.order_id}}/confirm-payment")
        assertThat(call.confirm?.title?.raw).isEqualTo("¿Confirmar el pago?")
        assertThat(call.confirm?.accept).isEqualTo("Confirmar")
        assertThat(call.success?.raw).isEqualTo("Pago confirmado")
        assertThat(call.then).containsExactly(Action.Refresh(emptyList()))
        assertThat(actions[7]).isInstanceOf(Action.Message::class.java)
        assertThat(actions[8]).isInstanceOf(Action.Copy::class.java)
        val seq = actions[9] as Action.Sequence
        assertThat(seq.actions.map { it::class }).containsExactly(Action.Message::class, Action.Back::class).inOrder()
        assertThat((actions[10] as Action.Unknown).type).isEqualTo("teletransportar")
        assertThat((actions[11] as Action.Navigate).sheet).isTrue()
    }

    @Test fun `los valores calculados se leen en orden y cada uno puede usar los anteriores`() {
        val doc = parseScreen(
            """{"schema": 1, "id": "x", "state": {"rango": "today"},
               "computed": {"validos": "{{pedidos.orders | where_not:'is_test'}}", "cuantos": "{{validos | count}}"},
               "body": [{"type": "text", "text": "{{cuantos}} pedidos"}]}""",
        ).doc!!
        assertThat(doc.computed.keys).containsExactly("validos", "cuantos").inOrder()
        val scope = screenScope(
            emptyMap(), doc.state, emptyMap(),
            mapOf("pedidos" to json("""{"orders": [{"is_test": false}, {"is_test": true}, {}]}""")), NOW, doc.computed, TEST_ENV,
        )
        assertThat(doc.body.single().template("text")!!.text(scope)).isEqualTo("2 pedidos")
    }

    @Test fun `una lista puede traer renglones fijos o una lista literal`() {
        val doc = parseScreen(
            """{"schema": 1, "id": "x", "body": [
                 {"type": "list", "children": [{"type": "list_item", "title": "Ventas"}, {"type": "list_item", "title": "Campañas"}]},
                 {"type": "list", "items": [{"n": "a"}, {"n": "b"}], "item": {"type": "text", "text": "{{item.n}}"}}]}""",
        ).doc!!
        assertThat(doc.body[0].children.map { it.template("title")!!.raw }).containsExactly("Ventas", "Campañas").inOrder()
        assertThat(doc.body[1].items(Scope.Empty, TEST_ENV)).hasSize(2)
    }

    @Test fun `el manifiesto de la app trae las pestanas`() {
        val manifest = requireNotNull(parseAppManifest("""{"schema": 1, "tabs": [{"screen": "mas", "label": "Más", "icon": "apps"}]}""").manifest)
        assertThat(manifest.tabs.single()).isEqualTo(TabSpec(screen = "mas", label = "Más", icon = "apps"))
        assertThat(parseAppManifest("no").manifest).isNull()
    }

    companion object {
        val CAMPANAS = """
        {
          "schema": 1,
          "id": "campanas",
          "title": "Campañas",
          "requires": 1,
          "state": { "filtro": "todas" },
          "data": {
            "campanas": { "get": "/api/marketing/campaigns", "query": { "status": "{{state.filtro | map:'todas=;*'}}" },
                          "refreshOn": ["marketing"], "every": 60 }
          },
          "topActions": [ { "icon": "refresh", "label": "Recargar", "action": { "type": "refresh" } } ],
          "fab": { "label": "Nueva campaña", "icon": "add", "action": { "type": "open_url", "url": "https://hubara.co" } },
          "body": [
            { "type": "chips", "bind": "state.filtro",
              "options": [ { "value": "todas", "label": "Todas" }, { "value": "sent", "label": "Enviadas" } ] },
            { "type": "section", "title": "{{campanas.campaigns | count | plural:'campaña':'campañas'}}", "children": [
              { "type": "list", "items": "{{campanas.campaigns}}", "as": "c", "key": "{{c.id}}",
                "item": { "type": "list_item", "title": "{{c.name}}", "subtitle": "{{c.status}}",
                          "visible": "{{c.name | present}}",
                          "action": { "type": "navigate", "screen": "campana", "params": { "campaign_id": "{{c.id}}" } } },
                "empty": { "type": "empty", "title": "No hay campañas" } }
            ] }
          ]
        }
        """.trimIndent()

        val ACCIONES = """
        {
          "schema": 1, "id": "acciones", "params": ["order_id"],
          "state": { "filtro": "todas" },
          "data": { "pedidos": { "get": "/api/orders/orders" } },
          "body": [
            { "type": "button", "text": "a", "action": { "type": "open_chat", "session": "{{params.order_id}}" } },
            { "type": "button", "text": "b", "action": { "type": "open_order", "order": "{{params.order_id}}" } },
            { "type": "button", "text": "c", "action": { "type": "open_url", "url": "https://wa.me/570000000000" } },
            { "type": "button", "text": "d", "action": { "type": "back" } },
            { "type": "button", "text": "e", "action": { "type": "refresh", "data": ["pedidos"] } },
            { "type": "button", "text": "f", "action": { "type": "set_state", "values": { "filtro": "nuevas" } } },
            { "type": "button", "text": "g", "action": { "type": "call", "method": "PATCH",
                "path": "/api/orders/orders/{{params.order_id}}/confirm-payment",
                "confirm": { "title": "¿Confirmar el pago?", "body": "Se le avisa al cliente.", "accept": "Confirmar" },
                "success": "Pago confirmado", "then": [ { "type": "refresh" } ] } },
            { "type": "button", "text": "h", "action": { "type": "message", "text": "Hola" } },
            { "type": "button", "text": "i", "action": { "type": "copy", "text": "{{params.order_id}}" } },
            { "type": "button", "text": "j", "action": [ { "type": "message", "text": "Listo" }, { "type": "back" } ] },
            { "type": "button", "text": "k", "action": { "type": "teletransportar" } },
            { "type": "button", "text": "l", "action": { "type": "navigate", "screen": "acciones", "params": { "order_id": "1" }, "sheet": true } }
          ]
        }
        """.trimIndent()
    }
}
