package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Lo que la CI le revisa a cada archivo de `android_operator/screens/` antes de publicarlo: que esta versión de la app
 * lo pueda pintar entero. Cada error dice DÓNDE está (`body[1].children[0]`) y qué arreglar, en español llano.
 */
class ValidationTest {

    private val known = mapOf("campana" to listOf("campaign_id"), "campanas" to emptyList(), "acciones" to listOf("order_id"))

    private fun problems(raw: String): List<String> {
        val parsed = parseScreen(raw)
        val doc = parsed.doc ?: return parsed.problems
        return parsed.problems + validateScreen(doc, known)
    }

    private fun screen(body: String, extra: String = "") =
        """{"schema": 1, "id": "prueba", $extra "data": {"pedidos": {"get": "/api/orders/orders"}}, "state": {"filtro": "todas"}, "body": [$body]}"""

    @Test fun `las pantallas de ejemplo no tienen errores`() {
        assertThat(problems(ScreenDocTest.CAMPANAS)).isEmpty()
        assertThat(problems(ScreenDocTest.ACCIONES).filterNot { "teletransportar" in it }).isEmpty()
    }

    @Test fun `componente desconocido`() {
        val p = problems(screen("""{"type": "text", "text": "a"}, {"type": "carrusel3d"}"""))
        assertThat(p).hasSize(1)
        assertThat(p.single()).contains("body[1]")
        assertThat(p.single()).contains("carrusel3d")
    }

    @Test fun `propiedad mal escrita`() {
        val p = problems(screen("""{"type": "text", "txt": "Hola", "text": "Hola"}"""))
        assertThat(p.single()).contains("«txt»")
    }

    @Test fun `falta una propiedad obligatoria`() {
        assertThat(problems(screen("""{"type": "text"}""")).single()).contains("«text»")
        assertThat(problems(screen("""{"type": "button", "text": "Sin acción"}""")).single()).contains("acción")
    }

    @Test fun `valor que no esta en la lista`() {
        assertThat(problems(screen("""{"type": "text", "text": "a", "style": "gigante"}""")).single()).contains("gigante")
        assertThat(problems(screen("""{"type": "text", "text": "a", "style": "{{state.filtro}}"}"""))).isEmpty()
    }

    @Test fun `icono que no existe`() {
        assertThat(problems(screen("""{"type": "icon", "name": "unicornio"}""")).single()).contains("unicornio")
    }

    @Test fun `plantillas rotas o que leen algo que no existe`() {
        assertThat(problems(screen("""{"type": "text", "text": "{{pedidos.orders | contar}}"}""")).single()).contains("contar")
        val list = """{"type": "list", "items": "{{pedidos.orders}}", "as": "pedido",
                       "item": {"type": "text", "text": "{{item.customer}}"}}"""
        assertThat(problems(screen(list)).single()).contains("«item»")
        val ok = """{"type": "list", "items": "{{pedidos.orders}}", "as": "pedido",
                     "item": {"type": "text", "text": "{{pedido.customer}} {{index}} {{params.x}} {{state.filtro}} {{form.y}} {{now}}"}}"""
        assertThat(problems(screen(ok))).isEmpty()
    }

    @Test fun `navegar a una pantalla que no existe o sin sus parametros`() {
        val unknown = """{"type": "button", "text": "a", "action": {"type": "navigate", "screen": "fantasma"}}"""
        assertThat(problems(screen(unknown)).single()).contains("fantasma")
        val missing = """{"type": "button", "text": "a", "action": {"type": "navigate", "screen": "campana"}}"""
        assertThat(problems(screen(missing)).single()).contains("campaign_id")
    }

    @Test fun `solo se llama a nuestro backend por ruta relativa`() {
        val abs = """{"schema": 1, "id": "x", "data": {"a": {"get": "https://otro.com/api/x"}}, "body": []}"""
        assertThat(problems(abs).single()).contains("/api/")
        val outside = """{"schema": 1, "id": "x", "data": {"a": {"get": "/admin/x"}}, "body": []}"""
        assertThat(problems(outside).single()).contains("/api/")
        val call = """{"type": "button", "text": "a", "action": {"type": "call", "method": "PATCH", "path": "//otro.com/api/x"}}"""
        assertThat(problems(screen(call)).single()).contains("/api/")
        val method = """{"type": "button", "text": "a", "action": {"type": "call", "method": "BORRAR", "path": "/api/x"}}"""
        assertThat(problems(screen(method)).single()).contains("BORRAR")
    }

    @Test fun `abrir enlaces solo https o tel`() {
        val http = """{"type": "button", "text": "a", "action": {"type": "open_url", "url": "http://inseguro.com"}}"""
        assertThat(problems(screen(http)).single()).contains("https")
        val tel = """{"type": "button", "text": "a", "action": {"type": "open_url", "url": "tel:+570000000000"}}"""
        assertThat(problems(screen(tel))).isEmpty()
    }

    @Test fun `estado, datos y entradas que no existen`() {
        val set = """{"type": "button", "text": "a", "action": {"type": "set_state", "values": {"filtr": "x"}}}"""
        assertThat(problems(screen(set)).single()).contains("filtr")
        val refresh = """{"type": "button", "text": "a", "action": {"type": "refresh", "data": ["pedido"]}}"""
        assertThat(problems(screen(refresh)).single()).contains("pedido")
        val chips = """{"type": "chips", "bind": "state.etapa", "options": [{"value": "a", "label": "A"}]}"""
        assertThat(problems(screen(chips)).single()).contains("etapa")
        val field = """{"type": "text_field", "bind": "guia", "label": "Guía"}"""
        assertThat(problems(screen(field)).single()).contains("form.")
    }

    @Test fun `valores calculados`() {
        val ok = """{"schema": 1, "id": "x", "data": {"pedidos": {"get": "/api/orders/orders"}},
                     "computed": {"validos": "{{pedidos.orders | where_not:'is_test'}}", "cuantos": "{{validos | count}}"},
                     "body": [{"type": "text", "text": "{{cuantos}}"}]}"""
        assertThat(problems(ok)).isEmpty()
        val adelantado = """{"schema": 1, "id": "x", "data": {"pedidos": {"get": "/api/orders/orders"}},
                     "computed": {"cuantos": "{{validos | count}}", "validos": "{{pedidos.orders}}"}, "body": []}"""
        assertThat(problems(adelantado).single()).contains("«validos»")
        val choca = """{"schema": 1, "id": "x", "data": {"pedidos": {"get": "/api/orders/orders"}},
                     "computed": {"pedidos": "{{pedidos.orders}}"}, "body": []}"""
        assertThat(problems(choca).single()).contains("pedidos")
        val enRuta = """{"schema": 1, "id": "x", "computed": {"c": "1"}, "data": {"p": {"get": "/api/x/{{c}}"}}, "body": []}"""
        assertThat(problems(enRuta).single()).contains("«c»")
    }

    @Test fun `listas fijas o repetidas, no las dos`() {
        val fija = """{"type": "list", "children": [{"type": "list_item", "title": "Ventas", "action": {"type": "back"}}]}"""
        assertThat(problems(screen(fija))).isEmpty()
        val literal = """{"type": "list", "items": [{"n": 1}], "item": {"type": "text", "text": "{{item.n}}"}}"""
        assertThat(problems(screen(literal))).isEmpty()
        assertThat(problems(screen("""{"type": "list"}""")).single()).contains("items")
        val ambas = """{"type": "list", "items": "{{pedidos.orders}}", "item": {"type": "text", "text": "x"}, "children": [{"type": "text", "text": "y"}]}"""
        assertThat(problems(screen(ambas)).single()).contains("children")
    }

    @Test fun `nombres reservados para fuentes de datos`() {
        val raw = """{"schema": 1, "id": "x", "data": {"state": {"get": "/api/x"}}, "body": []}"""
        assertThat(problems(raw).single()).contains("state")
    }

    @Test fun `version del catalogo`() {
        val future = """{"schema": 1, "id": "x", "requires": 99, "body": []}"""
        assertThat(problems(future).single()).contains("99")
    }

    @Test fun `el manifiesto solo apunta a pantallas que existen`() {
        val manifest = parseAppManifest(
            """{"schema": 1, "tabs": [{"screen": "fantasma", "label": "A", "icon": "apps"},
               {"screen": "campanas", "label": "B", "icon": "unicornio"}, {"screen": "campanas", "label": "C", "icon": "apps"}]}""",
        ).manifest!!
        val p = validateAppManifest(manifest, known)
        assertThat(p.joinToString()).contains("fantasma")
        assertThat(p.joinToString()).contains("unicornio")
        assertThat(p.joinToString()).contains("dos pestañas")
    }
}
