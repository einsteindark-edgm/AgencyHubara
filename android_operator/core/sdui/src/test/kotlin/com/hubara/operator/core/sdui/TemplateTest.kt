package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import org.junit.Assert.assertThrows
import org.junit.Test

class TemplateTest {

    private val scope = scopeOf(
        "cliente" to json("""{"nombre": "Laura", "ciudad": null}"""),
        "pedidos" to json("""{"orders": [{"customer": "Laura", "status": "new", "total_cop": 45000}, {"customer": "Sofía", "status": "ready", "total_cop": 30000}]}"""),
        "state" to json("""{"etapa": "ready"}"""),
    )

    @Test fun `interpola texto con datos`() {
        assertThat(Template.parse("Hola {{cliente.nombre}}, ¿cómo vas?").text(scope)).isEqualTo("Hola Laura, ¿cómo vas?")
    }

    @Test fun `una sola expresion devuelve el valor crudo y no texto`() {
        val value = Template.parse("{{ pedidos.orders }}").evaluate(scope)
        assertThat(value).isInstanceOf(JsonArray::class.java)
        assertThat(value.jsonArray).hasSize(2)
    }

    @Test fun `lo que no existe queda vacio, no rompe`() {
        assertThat(Template.parse("Ciudad: {{cliente.ciudad}}{{cliente.no.existe}}").text(scope)).isEqualTo("Ciudad: ")
        assertThat(Template.parse("{{nadie.sabe}}").evaluate(scope)).isEqualTo(JsonNull)
    }

    @Test fun `indices numericos en la ruta`() {
        assertThat(Template.parse("{{pedidos.orders.1.customer}}").text(scope)).isEqualTo("Sofía")
    }

    @Test fun `los numeros se escriben sin decimales de mas`() {
        val s = scopeOf("n" to json("""{"a": 45000, "b": 2.50, "c": 3.0}"""))
        assertThat(Template.parse("{{n.a}} {{n.b}} {{n.c}}").text(s)).isEqualTo("45000 2.5 3")
    }

    @Test fun `filtros encadenados`() {
        assertThat(Template.parse("{{pedidos.orders | count}} pedidos").text(scope)).isEqualTo("2 pedidos")
        assertThat(Template.parse("{{ pedidos.orders | sum:'total_cop' | money }}").text(scope)).isEqualTo("$75.000")
    }

    @Test fun `un argumento puede ser otra ruta`() {
        assertThat(Template.parse("{{pedidos.orders | where:'status':state.etapa | first | get:'customer'}}").text(scope))
            .isEqualTo("Sofía")
    }

    @Test fun `texto sin expresiones es literal`() {
        val t = Template.parse("Sin datos")
        assertThat(t.isExpressionOnly).isFalse()
        assertThat(t.hasExpressions).isFalse()
        assertThat(t.text(scope)).isEqualTo("Sin datos")
    }

    @Test fun `comillas protegen la barra y las llaves`() {
        assertThat(Template.parse("{{cliente.apodo | default:'a | b }}'}}").text(scope)).isEqualTo("a | b }}")
    }

    @Test fun `literales como operando`() {
        assertThat(Template.parse("{{ 'hola' | upper }}").text(scope)).isEqualTo("HOLA")
        assertThat(Template.parse("{{ 12 | plus:3 }}").evaluate(scope)).isEqualTo(JsonPrimitive(15))
    }

    @Test fun `errores de sintaxis y filtros desconocidos se detectan al leer`() {
        assertThrows(TemplateError::class.java) { Template.parse("{{ cliente.nombre | }}") }
        assertThrows(TemplateError::class.java) { Template.parse("Hola {{cliente.nombre") }
        assertThrows(TemplateError::class.java) { Template.parse("{{ cliente.nombre | inventado }}") }
        assertThrows(TemplateError::class.java) { Template.parse("{{ }}") }
    }

    @Test fun `las rutas que usa una plantilla se pueden listar`() {
        val t = Template.parse("{{pedidos.orders | where:'status':state.etapa | count}} de {{cliente.nombre}}")
        assertThat(t.roots()).containsExactly("pedidos", "state", "cliente")
    }

    @Test fun `un alcance hijo agrega el elemento de la lista`() {
        val item = scope.with("pedido", json("""{"customer": "Ana"}"""))
        assertThat(Template.parse("{{pedido.customer}} · {{cliente.nombre}}").text(item)).isEqualTo("Ana · Laura")
    }
}
