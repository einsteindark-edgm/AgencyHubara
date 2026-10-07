package com.hubara.operator.core.sdui

import java.math.BigDecimal
import java.time.ZoneId
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * De dónde salen los valores de `{{ … }}`: los datos de cada fuente por su id, `params`, `state`, `form` y, dentro de
 * una lista, el elemento con su alias. Lo que no existe es `null` y se pinta vacío: una pantalla nunca se cae por un
 * dato que falta.
 */
fun interface Scope {
    fun lookup(name: String): JsonElement?

    /** Un alcance hijo con un nombre más (el elemento de una lista); el resto se busca en este. */
    fun with(name: String, value: JsonElement): Scope = Scope { n -> if (n == name) value else lookup(n) }

    companion object {
        val Empty = Scope { null }
    }
}

fun scopeOf(vararg pairs: Pair<String, JsonElement>): Scope {
    val values = mapOf(*pairs)
    return Scope { values[it] }
}

/** El reloj y la zona de las fechas («hoy», «hace 5 min»). En los tests se fijan. */
data class Env(val nowMs: () -> Long = { System.currentTimeMillis() }, val zone: ZoneId = BOGOTA) {
    companion object {
        val BOGOTA: ZoneId = ZoneId.of("America/Bogota")
        val Default = Env()
    }
}

internal fun JsonElement?.isNullish(): Boolean = this == null || this is JsonNull

/** Verdadero para mostrar u ocultar: null, false, 0, "" y listas vacías son falso. */
val JsonElement?.truthy: Boolean
    get() = when (this) {
        null, JsonNull -> false
        is JsonPrimitive -> when {
            isString -> content.isNotEmpty() && content != "false"
            booleanOrNull != null -> booleanOrNull == true
            else -> content.toDoubleOrNull()?.let { it != 0.0 } ?: content.isNotEmpty()
        }
        is JsonArray -> isNotEmpty()
        is JsonObject -> isNotEmpty()
    }

/** «Hay algo»: a diferencia de [truthy], el 0 y el false sí cuentan como dato. */
val JsonElement?.present: Boolean
    get() = when (this) {
        null, JsonNull -> false
        is JsonPrimitive -> !isString || content.isNotEmpty()
        is JsonArray -> isNotEmpty()
        is JsonObject -> isNotEmpty()
    }

/** Cómo se pinta un valor en un texto. Los objetos no se pintan (nunca se muestra JSON al operador). */
fun JsonElement?.asText(): String = when (this) {
    null, JsonNull -> ""
    is JsonPrimitive -> if (isString) content else numberText(content) ?: content
    is JsonArray -> mapNotNull { it.asText().ifEmpty { null } }.joinToString(", ")
    is JsonObject -> ""
}

private fun numberText(content: String): String? =
    runCatching { BigDecimal(content).stripTrailingZeros().toPlainString() }.getOrNull()

internal fun JsonElement?.asNumber(): Double? = (this as? JsonPrimitive)?.takeIf { it.booleanOrNull == null || it.isString }
    ?.content?.toDoubleOrNull()

/** Un número de vuelta a JSON: entero si no tiene decimales (45000 y no 45000.0). */
internal fun num(value: Double): JsonPrimitive =
    if (value % 1.0 == 0.0 && kotlin.math.abs(value) < 9e15) JsonPrimitive(value.toLong()) else JsonPrimitive(value)

internal fun bool(value: Boolean) = JsonPrimitive(value)

internal fun text(value: String) = JsonPrimitive(value)

/** Un paso de la ruta: la clave de un objeto o el índice de una lista. */
internal fun JsonElement?.child(key: String): JsonElement? = when (this) {
    is JsonObject -> this[key]
    is JsonArray -> key.toIntOrNull()?.let { getOrNull(it) }
    else -> null
}

/** `a.b.0.c` sobre un valor. */
internal fun JsonElement?.at(path: String): JsonElement? =
    path.split('.').fold(this) { acc, key -> acc.child(key) ?: return null }

/** Iguales «a lo humano»: 3 y "3" son iguales; true y "true" también. */
internal fun looseEquals(a: JsonElement?, b: JsonElement?): Boolean {
    if (a.isNullish() || b.isNullish()) return a.isNullish() && b.isNullish()
    val na = a.asNumber()
    val nb = b.asNumber()
    if (na != null && nb != null) return na == nb
    return a.asText() == b.asText()
}

/** Orden para comparar y ordenar: números como números, el resto como texto sin mayúsculas. */
internal fun compareValues(a: JsonElement?, b: JsonElement?): Int {
    val na = a.asNumber()
    val nb = b.asNumber()
    return if (na != null && nb != null) na.compareTo(nb) else a.asText().lowercase().compareTo(b.asText().lowercase())
}

internal fun JsonElement?.asList(): List<JsonElement> = (this as? JsonArray).orEmpty()

/**
 * El alcance de una pantalla: cada fuente de datos por su id, `params`, `state`, `form` y `now` (milisegundos). Dentro
 * de una lista se le agrega el elemento con [Scope.with].
 */
fun screenScope(
    params: Map<String, String>,
    state: Map<String, JsonElement>,
    form: Map<String, JsonElement>,
    data: Map<String, JsonElement>,
    nowMs: Long,
    computed: Map<String, Template> = emptyMap(),
    env: Env = Env.Default,
    status: JsonElement? = null,
): Scope {
    val p = JsonObject(params.mapValues { JsonPrimitive(it.value) })
    val s = JsonObject(state)
    val f = JsonObject(form)
    val now = JsonPrimitive(nowMs)
    val base = Scope { name ->
        when (name) {
            "params" -> p
            "state" -> s
            "form" -> f
            "now" -> now
            "status" -> status
            else -> data[name]
        }
    }
    if (computed.isEmpty()) return base
    // En orden: cada valor calculado ve los datos y los calculados antes que él.
    val values = LinkedHashMap<String, JsonElement>()
    val scope = Scope { name -> values[name] ?: base.lookup(name) }
    computed.forEach { (name, template) -> values[name] = template.evaluate(scope, env) }
    return scope
}
