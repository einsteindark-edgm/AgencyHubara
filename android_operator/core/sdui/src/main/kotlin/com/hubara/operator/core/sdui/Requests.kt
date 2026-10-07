package com.hubara.operator.core.sdui

import java.net.URLEncoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Un pedido a NUESTRO backend: [path] es relativo (`/api/...?…`) y ya viene escapado. La app lo manda con el cliente
 * HTTP de siempre (el que pone el token solo para nuestro servidor).
 */
data class HttpCall(val method: String, val path: String, val body: JsonElement?)

/** La fuente de datos con los valores del momento. null si la ruta resultante no es segura (no se pide nada). */
fun DataSource.resolve(scope: Scope, env: Env = Env.Default): HttpCall? =
    if (app != null) null else buildPath(path, query, scope, env)?.let { HttpCall(method, it, null) }

/** Una fuente del teléfono con sus parámetros evaluados; null si la fuente es un GET. */
fun DataSource.appRequest(scope: Scope, env: Env = Env.Default): AppRequest? =
    app?.let { name -> AppRequest(name, appParams.mapValues { it.value.text(scope, env) }) }

fun Action.Call.resolve(scope: Scope, env: Env = Env.Default): HttpCall? =
    buildPath(path, query, scope, env)?.let { HttpCall(method, it, body?.let { b -> resolveJson(b, scope, env) }) }

/** Ruta relativa bajo `/api/`, sin dominio y sin `..` ni `.` como tramo. */
fun isSafeApiPath(path: String): Boolean {
    val route = path.substringBefore('?')
    return route.startsWith("/api/") && !route.startsWith("//") && "://" !in path && '\\' !in path &&
        route.split('/').none { it == ".." || it == "." }
}

internal fun buildPath(path: Template, query: Map<String, Template>, scope: Scope, env: Env): String? {
    // Cada valor va escapado como UN tramo: un «/» o un «?» que venga de un dato no cambia a dónde se llama.
    val route = path.text(scope, env, encode = ::encodeSegment)
    if (!isSafeApiPath(route)) return null
    val params = query.mapNotNull { (key, t) ->
        t.text(scope, env).takeIf { it.isNotEmpty() }?.let { "${encodeQuery(key)}=${encodeQuery(it)}" }
    }
    return if (params.isEmpty()) route else route + (if ('?' in route) "&" else "?") + params.joinToString("&")
}

/** El cuerpo de una llamada: cada texto es una plantilla; una expresión sola conserva su tipo (número, lista). */
fun resolveJson(el: JsonElement, scope: Scope, env: Env = Env.Default): JsonElement = when (el) {
    is JsonObject -> JsonObject(el.mapValues { (_, v) -> resolveJson(v, scope, env) })
    is JsonArray -> JsonArray(el.map { resolveJson(it, scope, env) })
    is JsonPrimitive -> if (el.isString) Template.parseOrLiteral(el.content).let { t ->
        if (t.isExpressionOnly) t.evaluate(scope, env) else JsonPrimitive(t.text(scope, env))
    } else el
}

// Con el NOMBRE del charset: la variante con `Charset` es de Java 10 y no existe en Android 11 (AndroidApiGuardTest).
private fun encodeSegment(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")

private fun encodeQuery(value: String): String = URLEncoder.encode(value, "UTF-8")
