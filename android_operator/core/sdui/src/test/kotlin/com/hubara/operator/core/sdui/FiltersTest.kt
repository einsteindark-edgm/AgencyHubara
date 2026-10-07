package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Test

/** Cada filtro del lenguaje de enlace, con el reloj fijo en Bogotá (1 oct 2026, 3:00 p. m.). */
class FiltersTest {

    private val pedidos = json(
        """[
          {"id": "o1", "customer": "Laura", "status": "new", "total_cop": 45000, "created_at_ms": ${bogota(2026, 10, 1, 9)}, "paid": true},
          {"id": "o2", "customer": "Sofía", "status": "ready", "total_cop": 30000, "created_at_ms": ${bogota(2026, 9, 30, 18)}, "paid": false},
          {"id": "o3", "customer": "Ana", "status": "new", "total_cop": 120000, "created_at_ms": ${bogota(2026, 9, 20, 10)}, "paid": true}
        ]""",
    )
    private val scope = scopeOf(
        "pedidos" to pedidos,
        "v" to json("""{"cero": 0, "vacio": "", "nada": null, "si": true, "no": false, "lista": [], "texto": "Hola Mundo"}"""),
        "state" to json("""{"etapa": "new"}"""),
    )

    private fun eval(expr: String): String = Template.parse("{{$expr}}").text(scope, TEST_ENV)
    private fun raw(expr: String) = Template.parse("{{$expr}}").evaluate(scope, TEST_ENV)

    @Test fun `plata en pesos y en dolares`() {
        assertThat(eval("45000 | money")).isEqualTo("$45.000")
        assertThat(eval("1234567 | money")).isEqualTo("$1.234.567")
        assertThat(eval("-5000 | money")).isEqualTo("-$5.000")
        assertThat(eval("1.5 | money:'USD'")).isEqualTo("US$1,50")
        assertThat(eval("v.nada | money")).isEqualTo("")
    }

    @Test fun `numeros, porcentajes y plurales`() {
        assertThat(eval("1234 | number")).isEqualTo("1.234")
        assertThat(eval("1234.56 | number:1")).isEqualTo("1.234,6")
        assertThat(eval("0.256 | percent")).isEqualTo("26 %")
        assertThat(eval("0.256 | percent:1")).isEqualTo("25,6 %")
        assertThat(eval("1 | plural:'pedido':'pedidos'")).isEqualTo("1 pedido")
        assertThat(eval("1200 | plural:'pedido':'pedidos'")).isEqualTo("1.200 pedidos")
        assertThat(eval("pedidos | count | plural:'pedido':'pedidos'")).isEqualTo("3 pedidos")
    }

    @Test fun `fechas en milisegundos, segundos o ISO`() {
        assertThat(eval("${bogota(2026, 9, 28, 15, 45)} | date")).isEqualTo("28 sep")
        assertThat(eval("'2025-12-24' | date")).isEqualTo("24 dic 2025")
        assertThat(eval("${bogota(2026, 9, 28, 15, 45) / 1000} | date")).isEqualTo("28 sep")
        assertThat(eval("'2026-09-28T20:45:00Z' | time")).isEqualTo("3:45 p. m.")
        assertThat(eval("${bogota(2026, 9, 28, 9, 5)} | datetime")).isEqualTo("28 sep, 9:05 a. m.")
        assertThat(eval("'no es fecha' | date")).isEqualTo("")
    }

    @Test fun `hace cuanto`() {
        assertThat(eval("${NOW - 30_000} | relative")).isEqualTo("ahora")
        assertThat(eval("${NOW - 5 * 60_000} | relative")).isEqualTo("hace 5 min")
        assertThat(eval("${NOW - 2 * 3_600_000} | relative")).isEqualTo("hace 2 h")
        assertThat(eval("${bogota(2026, 9, 30, 9)} | relative")).isEqualTo("ayer")
        assertThat(eval("${bogota(2026, 9, 28, 9)} | relative")).isEqualTo("hace 3 días")
        assertThat(eval("${bogota(2026, 9, 11, 9)} | relative")).isEqualTo("11 sep")
    }

    @Test fun `comparaciones y logica`() {
        assertThat(raw("state.etapa | eq:'new'")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("state.etapa | ne:'new'")).isEqualTo(JsonPrimitive(false))
        assertThat(raw("45000 | gt:40000")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("45000 | gte:45000")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("3 | lt:2")).isEqualTo(JsonPrimitive(false))
        assertThat(raw("3 | lte:3")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("v.si | not")).isEqualTo(JsonPrimitive(false))
        assertThat(raw("v.lista | empty")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("v.texto | present")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("v.vacio | present")).isEqualTo(JsonPrimitive(false))
        assertThat(raw("v.cero | present")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("state.etapa | in:'new,ready'")).isEqualTo(JsonPrimitive(true))
        assertThat(raw("v.si | and:v.no")).isEqualTo(JsonPrimitive(false))
        assertThat(raw("v.si | or:v.no")).isEqualTo(JsonPrimitive(true))
        assertThat(eval("v.si | if:'pagado':'pendiente'")).isEqualTo("pagado")
        assertThat(eval("v.no | if:'pagado'")).isEqualTo("")
        assertThat(eval("state.etapa | map:'new=Nuevo;ready=Listo;*=Otro'")).isEqualTo("Nuevo")
        assertThat(eval("'raro' | map:'new=Nuevo;*=Otro'")).isEqualTo("Otro")
        assertThat(eval("'raro' | map:'new=Nuevo'")).isEqualTo("raro")
        assertThat(eval("v.nada | default:'—'")).isEqualTo("—")
        assertThat(eval("v.vacio | default:'—'")).isEqualTo("—")
        assertThat(eval("v.cero | default:'—'")).isEqualTo("0")
        assertThat(eval("state.etapa | map:'new=secondary;*=neutral' | when:v.si:'danger'")).isEqualTo("danger")
        assertThat(eval("state.etapa | map:'new=secondary;*=neutral' | when:v.no:'danger'")).isEqualTo("secondary")
    }

    @Test fun `listas`() {
        assertThat(raw("pedidos | count")).isEqualTo(JsonPrimitive(3))
        assertThat(raw("pedidos | sum:'total_cop'")).isEqualTo(JsonPrimitive(195000))
        assertThat(raw("pedidos | where:'status':'new' | count")).isEqualTo(JsonPrimitive(2))
        assertThat(raw("pedidos | where:'paid' | count")).isEqualTo(JsonPrimitive(2))
        assertThat(raw("pedidos | where_not:'status':'new' | count")).isEqualTo(JsonPrimitive(1))
        assertThat(raw("pedidos | where_in:'status':'ready,shipping' | count")).isEqualTo(JsonPrimitive(1))
        assertThat(eval("pedidos | sort_desc:'total_cop' | first | get:'customer'")).isEqualTo("Ana")
        assertThat(eval("pedidos | sort:'total_cop' | first | get:'customer'")).isEqualTo("Sofía")
        assertThat(eval("pedidos | last | get:'id'")).isEqualTo("o3")
        assertThat(raw("pedidos | take:2 | count")).isEqualTo(JsonPrimitive(2))
        assertThat(eval("pedidos | pluck:'customer' | join:', '")).isEqualTo("Laura, Sofía, Ana")
        assertThat(raw("v.texto | count")).isEqualTo(JsonPrimitive(10))
        assertThat(raw("v.nada | count")).isEqualTo(JsonPrimitive(0))
    }

    @Test fun `ventanas de fecha en Bogota`() {
        assertThat(raw("pedidos | since:'created_at_ms':'today' | count")).isEqualTo(JsonPrimitive(1))
        assertThat(raw("pedidos | since:'created_at_ms':'2d' | count")).isEqualTo(JsonPrimitive(2))
        assertThat(raw("pedidos | since:'created_at_ms':'month' | count")).isEqualTo(JsonPrimitive(1))
        assertThat(raw("pedidos | since:'created_at_ms':'30d' | count")).isEqualTo(JsonPrimitive(3))
    }

    @Test fun `cuentas`() {
        assertThat(raw("10 | plus:5")).isEqualTo(JsonPrimitive(15))
        assertThat(raw("10 | minus:15")).isEqualTo(JsonPrimitive(-5))
        assertThat(raw("10 | times:1.5")).isEqualTo(JsonPrimitive(15))
        assertThat(raw("1500000 | divide:1000000")).isEqualTo(JsonPrimitive(1.5))
        assertThat(raw("10 | divide:0")).isEqualTo(JsonPrimitive(0))
        assertThat(raw("2.456 | round:1")).isEqualTo(JsonPrimitive(2.5))
        assertThat(raw("-3 | abs")).isEqualTo(JsonPrimitive(3))
        assertThat(raw("'12.000' | to_number")).isEqualTo(JsonPrimitive(12000))
    }

    @Test fun `texto`() {
        assertThat(eval("v.texto | upper")).isEqualTo("HOLA MUNDO")
        assertThat(eval("v.texto | lower")).isEqualTo("hola mundo")
        assertThat(eval("'laura prueba' | capitalize")).isEqualTo("Laura prueba")
        assertThat(eval("v.texto | truncate:6")).isEqualTo("Hola …")
        assertThat(eval("'  hola  ' | trim")).isEqualTo("hola")
        assertThat(eval("'#41' | replace:'#':''")).isEqualTo("41")
        assertThat(raw("12 | to_text")).isEqualTo(JsonPrimitive("12"))
    }

    @Test fun `hora de la bandeja como WhatsApp`() {
        assertThat(eval("${bogota(2026, 10, 1, 14, 42)} | list_time")).isEqualTo("2:42 p. m.")
        assertThat(eval("${bogota(2026, 9, 30, 9)} | list_time")).isEqualTo("Ayer")
        assertThat(eval("${bogota(2026, 9, 28, 9)} | list_time")).isEqualTo("lun")
        assertThat(eval("${bogota(2026, 9, 11, 9)} | list_time")).isEqualTo("11 sep")
        assertThat(eval("'2025-12-24' | list_time")).isEqualTo("24 dic 2025")
    }

    @Test fun `llenar huecos de un texto y saber que falta`() {
        val s = scopeOf(
            "t" to json("""{"body": "Hola {{nombre}}, {{tema}}.", "fallback": {"nombre": "[Nombre]", "tema": "[Tema]"}}"""),
            "form" to json("""{"nombre": "Laura", "tema": ""}"""),
            "vars" to json("""["nombre", "tema"]"""),
        )
        assertThat(Template.parse("{{t.body | fill:form:t.fallback}}").text(s, TEST_ENV)).isEqualTo("Hola Laura, [Tema].")
        assertThat(Template.parse("{{t.body | fill:form}}").text(s, TEST_ENV)).isEqualTo("Hola Laura, {{tema}}.")
        assertThat(Template.parse("{{vars | missing:form}}").evaluate(s, TEST_ENV)).isEqualTo(json("""["tema"]"""))
        assertThat(Template.parse("{{vars | missing:form | empty}}").evaluate(s, TEST_ENV)).isEqualTo(JsonPrimitive(false))
        assertThat(Template.parse("{{'https://envia.co/1' | starts_with:'http'}}").evaluate(s, TEST_ENV)).isEqualTo(JsonPrimitive(true))
        assertThat(Template.parse("{{'envia.co' | starts_with:'http'}}").evaluate(s, TEST_ENV)).isEqualTo(JsonPrimitive(false))
    }

    @Test fun `todos los filtros documentados existen`() {
        assertThat(Filters.names).containsAtLeast(
            "money", "number", "percent", "plural", "date", "time", "datetime", "relative",
            "eq", "ne", "gt", "gte", "lt", "lte", "not", "empty", "present", "in", "and", "or", "if", "map", "default",
            "count", "sum", "where", "where_not", "where_in", "sort", "sort_desc", "first", "last", "take", "pluck",
            "join", "get", "since", "plus", "minus", "times", "divide", "round", "abs", "to_number", "to_text",
            "upper", "lower", "capitalize", "truncate", "trim", "replace",
        )
    }
}
