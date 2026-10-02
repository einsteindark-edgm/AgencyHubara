package com.hubara.operator.core.sdui

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Un filtro de `{{ valor | filtro:arg }}`: cuántos argumentos recibe, qué hace (para la guía) y la función. */
internal class FilterDef(
    val name: String,
    val minArgs: Int,
    val maxArgs: Int,
    val group: String,
    val doc: String,
    private val fn: (value: JsonElement, args: List<JsonElement>, env: Env) -> JsonElement,
) {
    fun apply(value: JsonElement, args: List<JsonElement>, env: Env): JsonElement =
        runCatching { fn(value, args, env) }.getOrDefault(JsonNull)

    fun arity(): String = when {
        minArgs == maxArgs && minArgs == 0 -> "ningún argumento"
        minArgs == maxArgs -> "$minArgs argumento(s)"
        else -> "entre $minArgs y $maxArgs argumentos"
    }
}

/**
 * La lista CERRADA de filtros. Agregar uno es agregarlo aquí (con su test en `FiltersTest` y su línea en la guía
 * `android_operator/screens/README.md`); una pantalla que usa un filtro que esta versión de la app no conoce no pasa
 * la validación de la CI.
 */
object Filters {
    private val defs: Map<String, FilterDef> = listOf(
        // ── Formato ─────────────────────────────────────────────────────────────────────────────────
        f("money", 0, 1, "Formato", "Plata: `45000` → «$45.000». Con `'USD'` → «US$1,50».") { v, a, _ ->
            v.asNumber()?.let { text(formatMoney(it, a.getOrNull(0)?.asText()?.ifBlank { null } ?: "COP")) } ?: text("")
        },
        f("number", 0, 1, "Formato", "Número con punto de miles: `1234` → «1.234». `number:1` deja un decimal.") { v, a, _ ->
            v.asNumber()?.let { text(formatNumber(it, a.intArg(0) ?: 0)) } ?: text("")
        },
        f("percent", 0, 1, "Formato", "Proporción a porcentaje: `0.256` → «26 %». `percent:1` → «25,6 %».") { v, a, _ ->
            v.asNumber()?.let { text(formatNumber(it * 100, a.intArg(0) ?: 0) + " %") } ?: text("")
        },
        f("plural", 1, 2, "Formato", "`3 | plural:'pedido':'pedidos'` → «3 pedidos»; con 1 → «1 pedido».") { v, a, _ ->
            val n = v.asNumber() ?: 0.0
            val one = a[0].asText()
            val many = a.getOrNull(1)?.asText() ?: "${one}s"
            text("${formatNumber(n, if (n % 1.0 == 0.0) 0 else 1)} ${if (n == 1.0) one else many}")
        },
        f("date", 0, 0, "Formato", "Fecha corta en Bogotá: «28 sep» (con el año si no es este). Acepta milisegundos, segundos o ISO.") { v, _, e ->
            toInstant(v, e.zone)?.let { text(formatDate(it, e)) } ?: text("")
        },
        f("time", 0, 0, "Formato", "Hora en Bogotá: «3:45 p. m.».") { v, _, e ->
            toInstant(v, e.zone)?.let { text(formatTime(it, e)) } ?: text("")
        },
        f("datetime", 0, 0, "Formato", "«28 sep, 3:45 p. m.».") { v, _, e ->
            toInstant(v, e.zone)?.let { text("${formatDate(it, e)}, ${formatTime(it, e)}") } ?: text("")
        },
        f("relative", 0, 0, "Formato", "«ahora», «hace 5 min», «hace 2 h», «ayer», «hace 3 días» y después la fecha.") { v, _, e ->
            toInstant(v, e.zone)?.let { text(formatRelative(it, e)) } ?: text("")
        },
        // ── Lógica (para `visible`, `enabled` y textos condicionales) ─────────────────────────────────────
        f("eq", 1, 1, "Lógica", "¿Es igual? `state.etapa | eq:'new'`.") { v, a, _ -> bool(looseEquals(v, a[0])) },
        f("ne", 1, 1, "Lógica", "¿Es distinto?") { v, a, _ -> bool(!looseEquals(v, a[0])) },
        f("gt", 1, 1, "Lógica", "¿Es mayor?") { v, a, _ -> bool(compareValues(v, a[0]) > 0) },
        f("gte", 1, 1, "Lógica", "¿Es mayor o igual?") { v, a, _ -> bool(compareValues(v, a[0]) >= 0) },
        f("lt", 1, 1, "Lógica", "¿Es menor?") { v, a, _ -> bool(compareValues(v, a[0]) < 0) },
        f("lte", 1, 1, "Lógica", "¿Es menor o igual?") { v, a, _ -> bool(compareValues(v, a[0]) <= 0) },
        f("not", 0, 0, "Lógica", "Lo contrario: `item.paid | not`.") { v, _, _ -> bool(!v.truthy) },
        f("empty", 0, 0, "Lógica", "¿Está vacío? (null, «», lista vacía)") { v, _, _ -> bool(!v.present) },
        f("present", 0, 0, "Lógica", "¿Tiene algo? (el 0 sí cuenta)") { v, _, _ -> bool(v.present) },
        f("in", 1, 1, "Lógica", "¿Está en la lista? `item.status | in:'new,ready'`.") { v, a, _ ->
            bool(v.asText() in a[0].asText().split(',').map { it.trim() })
        },
        f("and", 1, 1, "Lógica", "Y: `item.paid | and:item.shipped`.") { v, a, _ -> bool(v.truthy && a[0].truthy) },
        f("or", 1, 1, "Lógica", "O: `item.overdue | or:item.flagged`.") { v, a, _ -> bool(v.truthy || a[0].truthy) },
        f("if", 1, 2, "Lógica", "Si es verdadero, el primero; si no, el segundo: `item.paid | if:'Pagado':'Pendiente'`.") { v, a, _ ->
            if (v.truthy) a[0] else a.getOrNull(1) ?: text("")
        },
        f("map", 1, 1, "Lógica", "Traduce valores: `item.status | map:'new=Nuevo;ready=Listo;*=Otro'` (`*` = cualquier otro).") { v, a, _ ->
            val table = a[0].asText().split(';').mapNotNull { pair ->
                pair.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0].trim() to it[1].trim() }
            }.toMap()
            table[v.asText()]?.let(::text) ?: table["*"]?.let(::text) ?: v
        },
        f("when", 2, 2, "Lógica", "Si la condición da verdadero, este valor; si no, sigue el que venía: `o.status | map:'…' | when:o.overdue:'danger'`.") { v, a, _ ->
            if (a[0].truthy) a[1] else v
        },
        f("default", 1, 1, "Lógica", "Si no hay dato (null o «»), este: `cliente.ciudad | default:'—'`.") { v, a, _ ->
            if (v.present) v else a[0]
        },
        // ── Listas ──────────────────────────────────────────────────────────────────────────────────
        f("count", 0, 0, "Listas", "Cuántos hay (o el largo de un texto).") { v, _, _ ->
            when (v) {
                JsonNull -> num(0.0)
                is JsonArray -> num(v.size.toDouble())
                is JsonObject -> num(v.size.toDouble())
                is JsonPrimitive -> num(if (v.isString) v.content.length.toDouble() else 1.0)
                else -> num(0.0)
            }
        },
        f("sum", 0, 1, "Listas", "Suma un campo: `pedidos.orders | sum:'total_cop'`.") { v, a, _ ->
            val field = a.getOrNull(0)?.asText()
            num(v.asList().sumOf { (if (field == null) it else it.at(field)).asNumber() ?: 0.0 })
        },
        f("where", 1, 2, "Listas", "Solo los que cumplen: `where:'status':'new'`; sin valor, los que tienen el campo en verdadero.") { v, a, _ ->
            val field = a[0].asText()
            JsonArray(v.asList().filter { item -> if (a.size == 1) item.at(field).truthy else looseEquals(item.at(field), a[1]) })
        },
        f("where_not", 1, 2, "Listas", "Los que NO cumplen.") { v, a, _ ->
            val field = a[0].asText()
            JsonArray(v.asList().filterNot { item -> if (a.size == 1) item.at(field).truthy else looseEquals(item.at(field), a[1]) })
        },
        f("where_in", 2, 2, "Listas", "Los que tienen uno de esos valores: `where_in:'status':'ready,shipping'`.") { v, a, _ ->
            val field = a[0].asText()
            val allowed = a[1].asText().split(',').map { it.trim() }.toSet()
            JsonArray(v.asList().filter { it.at(field).asText() in allowed })
        },
        f("since", 2, 2, "Listas", "Los de una ventana de fechas en Bogotá: `since:'created_at_ms':'today'` (`week`, `month`, `7d`, `30d`…).") { v, a, e ->
            val field = a[0].asText()
            val start = windowStart(a[1].asText(), e) ?: return@f JsonArray(emptyList())
            JsonArray(v.asList().filter { item -> toInstant(item.at(field), e.zone)?.let { !it.isBefore(start) } == true })
        },
        f("sort", 1, 1, "Listas", "Ordena de menor a mayor por un campo.") { v, a, _ ->
            val field = a[0].asText()
            JsonArray(v.asList().sortedWith { x, y -> compareNullsLast(x.at(field), y.at(field)) })
        },
        f("sort_desc", 1, 1, "Listas", "Ordena de mayor a menor por un campo.") { v, a, _ ->
            val field = a[0].asText()
            JsonArray(v.asList().sortedWith { x, y -> compareNullsLast(y.at(field), x.at(field), nullsFirst = true) })
        },
        f("take", 1, 1, "Listas", "Los primeros N.") { v, a, _ -> JsonArray(v.asList().take(a.intArg(0) ?: 0)) },
        f("first", 0, 0, "Listas", "El primero.") { v, _, _ -> v.asList().firstOrNull() ?: JsonNull },
        f("last", 0, 0, "Listas", "El último.") { v, _, _ -> v.asList().lastOrNull() ?: JsonNull },
        f("pluck", 1, 1, "Listas", "Saca un campo de cada uno: `pluck:'customer'`.") { v, a, _ ->
            val field = a[0].asText()
            JsonArray(v.asList().map { it.at(field) ?: JsonNull })
        },
        f("join", 0, 1, "Listas", "Une en un texto (por defecto con «, »).") { v, a, _ ->
            text(v.asList().map { it.asText() }.filter { it.isNotEmpty() }.joinToString(a.getOrNull(0)?.asText() ?: ", "))
        },
        f("get", 1, 1, "Listas", "Un campo del valor: `pedidos.orders | first | get:'customer'`.") { v, a, _ ->
            v.at(a[0].asText()) ?: JsonNull
        },
        // ── Cuentas ─────────────────────────────────────────────────────────────────────────────────
        f("plus", 1, 1, "Cuentas", "Suma.") { v, a, _ -> num((v.asNumber() ?: 0.0) + (a[0].asNumber() ?: 0.0)) },
        f("minus", 1, 1, "Cuentas", "Resta.") { v, a, _ -> num((v.asNumber() ?: 0.0) - (a[0].asNumber() ?: 0.0)) },
        f("times", 1, 1, "Cuentas", "Multiplica.") { v, a, _ -> num((v.asNumber() ?: 0.0) * (a[0].asNumber() ?: 0.0)) },
        f("divide", 1, 1, "Cuentas", "Divide (entre 0 da 0): `spent_usd_micros | divide:1000000`.") { v, a, _ ->
            val d = a[0].asNumber() ?: 0.0
            num(if (d == 0.0) 0.0 else (v.asNumber() ?: 0.0) / d)
        },
        f("round", 0, 1, "Cuentas", "Redondea (con N decimales).") { v, a, _ ->
            val n = v.asNumber() ?: return@f JsonNull
            num(java.math.BigDecimal.valueOf(n).setScale(a.intArg(0) ?: 0, java.math.RoundingMode.HALF_UP).toDouble())
        },
        f("abs", 0, 0, "Cuentas", "Sin signo.") { v, _, _ -> v.asNumber()?.let { num(kotlin.math.abs(it)) } ?: JsonNull },
        f("to_number", 0, 0, "Cuentas", "Texto a número, como lo escribe el operador: «12.000» → 12000, «12,5» → 12.5.") { v, _, _ ->
            if (v is JsonPrimitive && !v.isString) v
            else v.asText().filter { it.isDigit() || it == '-' || it == ',' }.replace(',', '.').toDoubleOrNull()?.let(::num) ?: JsonNull
        },
        // ── Texto ───────────────────────────────────────────────────────────────────────────────────
        f("to_text", 0, 0, "Texto", "Número a texto, tal cual.") { v, _, _ -> text(v.asText()) },
        f("upper", 0, 0, "Texto", "MAYÚSCULAS.") { v, _, _ -> text(v.asText().uppercase()) },
        f("lower", 0, 0, "Texto", "minúsculas.") { v, _, _ -> text(v.asText().lowercase()) },
        f("capitalize", 0, 0, "Texto", "Primera letra en mayúscula.") { v, _, _ -> text(v.asText().replaceFirstChar { it.uppercase() }) },
        f("truncate", 1, 1, "Texto", "Corta a N letras con «…».") { v, a, _ ->
            val s = v.asText()
            val n = a.intArg(0) ?: s.length
            text(if (s.length <= n) s else s.take((n - 1).coerceAtLeast(0)) + "…")
        },
        f("trim", 0, 0, "Texto", "Sin espacios a los lados.") { v, _, _ -> text(v.asText().trim()) },
        f("list_time", 0, 0, "Formato", "La hora de una fila de la bandeja, como WhatsApp: hoy la hora, «Ayer», el día de la semana y luego la fecha.") { v, _, e ->
            toInstant(v, e.zone)?.let { text(formatListTime(it, e)) } ?: text("")
        },
        f("fill", 1, 2, "Texto", "Llena los huecos `{{clave}}` de un texto con un objeto (`fill:form`); los que faltan, con el segundo (`fill:form:t.fallback`).") { v, a, _ ->
            val values = a[0] as? JsonObject
            val fallback = a.getOrNull(1) as? JsonObject
            text(Regex("\\{\\{\\s*([A-Za-z0-9_]+)\\s*\\}\\}").replace(v.asText()) { m ->
                val key = m.groupValues[1]
                values?.get(key)?.asText()?.ifEmpty { null } ?: fallback?.get(key)?.asText()?.ifEmpty { null } ?: m.value
            })
        },
        f("missing", 1, 1, "Listas", "De una lista de claves, las que están vacías en el objeto: `variable_names | missing:form`.") { v, a, _ ->
            val values = a[0] as? JsonObject
            JsonArray(v.asList().filter { key -> values?.get(key.asText())?.asText().isNullOrBlank() })
        },
        f("starts_with", 1, 1, "Lógica", "¿Empieza con…? `form.guia | starts_with:'http'`.") { v, a, _ -> bool(v.asText().startsWith(a[0].asText())) },
        f("replace", 2, 2, "Texto", "Reemplaza: `display_id | replace:'#':''`.") { v, a, _ -> text(v.asText().replace(a[0].asText(), a[1].asText())) },
    ).associateBy { it.name }

    val names: Set<String> get() = defs.keys

    internal fun def(name: String): FilterDef? = defs[name]

    /** Para la guía: grupo → (filtro, descripción). */
    fun catalog(): Map<String, List<Pair<String, String>>> =
        defs.values.groupBy({ it.group }, { it.name to it.doc })

    private fun f(
        name: String,
        min: Int,
        max: Int,
        group: String,
        doc: String,
        fn: (JsonElement, List<JsonElement>, Env) -> JsonElement,
    ) = FilterDef(name, min, max, group, doc, fn)
}

private fun List<JsonElement>.intArg(index: Int): Int? = getOrNull(index)?.asNumber()?.toInt()

private fun compareNullsLast(a: JsonElement?, b: JsonElement?, nullsFirst: Boolean = false): Int = when {
    a.isNullish() && b.isNullish() -> 0
    a.isNullish() -> if (nullsFirst) -1 else 1
    b.isNullish() -> if (nullsFirst) 1 else -1
    else -> compareValues(a, b)
}
