package com.hubara.operator.core.sdui

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

/** Una plantilla mal escrita (llaves sin cerrar, filtro que no existe). La validación de la CI la reporta. */
class TemplateError(message: String) : IllegalArgumentException(message)

/**
 * Texto con expresiones `{{ … }}`: `"Pedido #{{pedido.display_id}} · {{pedido.total_cop | money}}"`.
 *
 * Una expresión es una ruta (`pedidos.orders.0.customer`) o un literal (`'hola'`, `12`, `true`), seguida de filtros
 * con `|` y sus argumentos con `:` (`where:'status':state.etapa`). No hay código: solo rutas, literales y la lista
 * cerrada de [Filters]. Si la plantilla es UNA sola expresión, [evaluate] devuelve el valor crudo (una lista, un
 * número); si mezcla texto, devuelve el texto armado.
 */
class Template private constructor(val raw: String, private val parts: List<Any>) {

    /** Toda la plantilla es una expresión: `"{{pedidos.orders}}"`. */
    val isExpressionOnly: Boolean = parts.size == 1 && parts[0] is Expr

    val hasExpressions: Boolean = parts.any { it is Expr }

    fun evaluate(scope: Scope, env: Env = Env.Default): JsonElement = when {
        isExpressionOnly -> (parts[0] as Expr).evaluate(scope, env)
        else -> JsonPrimitive(text(scope, env))
    }

    /** El texto armado. [encode] se aplica solo a lo que sale de las expresiones (para escapar valores en una ruta). */
    fun text(scope: Scope, env: Env = Env.Default, encode: ((String) -> String)? = null): String = buildString {
        parts.forEach { part ->
            append(
                if (part is Expr) part.evaluate(scope, env).asText().let { encode?.invoke(it) ?: it } else part as String,
            )
        }
    }

    /** El texto fijo antes de la primera expresión (`https://` en `https://wa.me/{{…}}`). */
    fun literalPrefix(): String = parts.takeWhile { it is String }.joinToString("") { it as String }

    /** Los nombres de primer nivel que lee (`pedidos`, `state`…): así se sabe qué fuente de datos usa cada cosa. */
    fun roots(): Set<String> = parts.filterIsInstance<Expr>().flatMapTo(LinkedHashSet()) { it.roots() }

    /** Las rutas completas que lee (`state.filtro` → [state, filtro]), para validar las claves de `state`. */
    internal fun paths(): List<List<String>> = parts.filterIsInstance<Expr>().flatMap { it.paths() }

    override fun toString() = raw

    /** Dos plantillas con el mismo texto son la misma (así se sabe si una fuente de datos cambió entre versiones). */
    override fun equals(other: Any?) = other is Template && other.raw == raw

    override fun hashCode() = raw.hashCode()

    companion object {
        fun parse(raw: String): Template = Template(raw, split(raw))

        /** Para pintar en la app: una plantilla rota se muestra tal cual en vez de romper la pantalla. */
        fun parseOrLiteral(raw: String): Template = runCatching { parse(raw) }.getOrElse { Template(raw, listOf(raw)) }

        private fun split(raw: String): List<Any> {
            val parts = mutableListOf<Any>()
            var i = 0
            val literal = StringBuilder()
            while (i < raw.length) {
                val open = raw.indexOf("{{", i)
                if (open < 0) {
                    literal.append(raw, i, raw.length)
                    break
                }
                literal.append(raw, i, open)
                val close = findClose(raw, open + 2) ?: throw TemplateError("Falta cerrar «{{» en «$raw»")
                if (literal.isNotEmpty()) {
                    parts += literal.toString()
                    literal.clear()
                }
                parts += ExprParser(raw.substring(open + 2, close), raw).parse()
                i = close + 2
            }
            if (literal.isNotEmpty()) parts += literal.toString()
            return parts
        }

        /** El `}}` que cierra, saltando lo que esté entre comillas. */
        private fun findClose(raw: String, from: Int): Int? {
            var quote: Char? = null
            var i = from
            while (i < raw.length) {
                val c = raw[i]
                when {
                    quote != null -> if (c == quote) quote = null
                    c == '\'' || c == '"' -> quote = c
                    c == '}' && raw.startsWith("}}", i) -> return i
                }
                i++
            }
            return null
        }
    }
}

internal sealed interface Operand {
    fun evaluate(scope: Scope): JsonElement

    data class Path(val segments: List<String>) : Operand {
        override fun evaluate(scope: Scope): JsonElement =
            segments.drop(1).fold(scope.lookup(segments.first())) { acc, key -> acc.child(key) ?: return JsonNull } ?: JsonNull
    }

    data class Literal(val value: JsonElement) : Operand {
        override fun evaluate(scope: Scope) = value
    }
}

internal class FilterCall(val def: FilterDef, val args: List<Operand>)

internal class Expr(private val operand: Operand, private val filters: List<FilterCall>) {
    fun evaluate(scope: Scope, env: Env): JsonElement =
        filters.fold(operand.evaluate(scope)) { value, call -> call.def.apply(value, call.args.map { it.evaluate(scope) }, env) }

    fun roots(): List<String> = paths().map { it.first() }

    fun paths(): List<List<String>> = (listOf(operand) + filters.flatMap { it.args })
        .filterIsInstance<Operand.Path>().map { it.segments }
}

/** `ruta | filtro:arg:arg | filtro`. Los argumentos son literales o rutas. */
private class ExprParser(private val src: String, private val template: String) {
    private var pos = 0

    fun parse(): Expr {
        skipSpaces()
        if (pos >= src.length) throw error("expresión vacía")
        val operand = operand()
        val filters = mutableListOf<FilterCall>()
        skipSpaces()
        while (pos < src.length) {
            expect('|')
            skipSpaces()
            val name = identifier() ?: throw error("falta el nombre del filtro después de «|»")
            val def = Filters.def(name) ?: throw error("filtro desconocido «$name»")
            val args = mutableListOf<Operand>()
            skipSpaces()
            while (peek() == ':') {
                pos++
                skipSpaces()
                args += operand()
                skipSpaces()
            }
            if (args.size !in def.minArgs..def.maxArgs) {
                throw error("«$name» recibe ${def.arity()} y tiene ${args.size}")
            }
            filters += FilterCall(def, args)
        }
        return Expr(operand, filters)
    }

    private fun operand(): Operand {
        val c = peek() ?: throw error("falta un valor")
        return when {
            c == '\'' || c == '"' -> Operand.Literal(JsonPrimitive(quoted(c)))
            c == '-' || c.isDigit() -> Operand.Literal(number())
            else -> {
                val path = path() ?: throw error("no entiendo «${src.substring(pos)}»")
                when (path) {
                    "true" -> Operand.Literal(JsonPrimitive(true))
                    "false" -> Operand.Literal(JsonPrimitive(false))
                    "null" -> Operand.Literal(JsonNull)
                    else -> Operand.Path(path.split('.'))
                }
            }
        }
    }

    private fun quoted(q: Char): String {
        val end = src.indexOf(q, pos + 1)
        if (end < 0) throw error("falta cerrar la comilla")
        return src.substring(pos + 1, end).also { pos = end + 1 }
    }

    private fun number(): JsonElement {
        val start = pos
        if (peek() == '-') pos++
        while (pos < src.length && (src[pos].isDigit() || src[pos] == '.')) pos++
        val raw = src.substring(start, pos)
        val value = raw.toDoubleOrNull() ?: throw error("número inválido «$raw»")
        return num(value)
    }

    private fun identifier(): String? {
        val start = pos
        while (pos < src.length && (src[pos].isLetterOrDigit() || src[pos] == '_')) pos++
        return src.substring(start, pos).takeIf { it.isNotEmpty() && !it[0].isDigit() }
    }

    /** `a.b.0.c`: el primer tramo es un nombre; los siguientes pueden ser índices. */
    private fun path(): String? {
        val first = identifier() ?: return null
        val out = StringBuilder(first)
        while (peek() == '.') {
            pos++
            val start = pos
            while (pos < src.length && (src[pos].isLetterOrDigit() || src[pos] == '_')) pos++
            if (pos == start) throw error("ruta incompleta después de «.»")
            out.append('.').append(src, start, pos)
        }
        return out.toString()
    }

    private fun expect(c: Char) {
        if (peek() != c) throw error("esperaba «$c» y encontré «${src.substring(pos)}»")
        pos++
    }

    private fun peek(): Char? = src.getOrNull(pos)

    private fun skipSpaces() {
        while (pos < src.length && src[pos].isWhitespace()) pos++
    }

    private fun error(what: String) = TemplateError("En «$template»: $what")
}
