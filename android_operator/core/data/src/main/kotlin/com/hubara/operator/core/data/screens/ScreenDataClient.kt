package com.hubara.operator.core.data.screens

import com.hubara.operator.core.network.di.PLACEHOLDER_BASE_URL
import com.hubara.operator.core.sdui.HttpCall
import com.hubara.operator.core.sdui.isSafeApiPath
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Un error del backend, con el texto que se le puede mostrar al operador. */
class ScreenCallError(val code: Int, message: String) : Exception(message)

/** Lo que el ViewModel de una pantalla usa para pedir datos y ejecutar llamadas. */
fun interface ScreenData {
    suspend fun execute(call: HttpCall): Result<JsonElement>
}

private val JSON = "application/json; charset=utf-8".toMediaType()

/**
 * Pide a NUESTRO backend lo que declara una pantalla. Usa el cliente HTTP de siempre: va al backend vigente (la
 * dirección comodín la cambia el interceptor), con el token del operador, y si el token venció lo refresca solo.
 */
@Singleton
class ScreenDataClient @Inject constructor(private val client: OkHttpClient) : ScreenData {

    override suspend fun execute(call: HttpCall): Result<JsonElement> = withContext(Dispatchers.IO) {
        // Segunda revisión (la primera es al armar la ruta): ni una ruta absoluta ni una con `..` salen del teléfono.
        if (!isSafeApiPath(call.path)) return@withContext Result.failure(ScreenCallError(0, "Ruta no permitida."))
        val url = PLACEHOLDER_BASE_URL.resolve(call.path)
            ?: return@withContext Result.failure(ScreenCallError(0, "Ruta no permitida."))
        val body = when {
            call.body != null -> call.body.toString().toRequestBody(JSON)
            call.method in setOf("POST", "PUT", "PATCH") -> "{}".toRequestBody(JSON)
            else -> null
        }
        runCatching {
            client.newCall(Request.Builder().url(url).method(call.method, body).build()).execute().use { response ->
                val text = response.body.string()
                if (response.isSuccessful) {
                    val body = if (text.isBlank()) JsonNull else runCatching { Json.parseToJsonElement(text) }.getOrElse { JsonPrimitive(text) }
                    // Los endpoints de pedidos (confirm-payment, stage…) rechazan con 200 + `"success": false`: no es un éxito.
                    if (body is JsonObject && (body["success"] as? JsonPrimitive)?.content == "false") {
                        throw ScreenCallError(response.code, detail(text) ?: "No se pudo completar.")
                    }
                    body
                } else {
                    throw ScreenCallError(response.code, detail(text) ?: "El servidor respondió ${response.code}.")
                }
            }
        }.recoverCatching { e ->
            throw if (e is ScreenCallError) e else ScreenCallError(0, "Sin conexión con el servidor.")
        }
    }

    /** FastAPI manda `{"detail": "…"}` o `{"detail": [{"msg": "…"}]}`; otros endpoints `{"error": "…"}`. */
    private fun detail(text: String): String? {
        val root = runCatching { Json.parseToJsonElement(text) }.getOrNull() as? JsonObject ?: return null
        val raw = listOf("detail", "error_detail", "error", "message").firstNotNullOfOrNull { key -> root[key]?.takeIf { it !is JsonNull } }
            ?: return null
        return when (raw) {
            is JsonPrimitive -> raw.content
            is JsonArray -> ((raw.firstOrNull() as? JsonObject)?.get("msg") as? JsonPrimitive)?.content
            else -> null
        }?.replace(CODE_PREFIX, "")?.takeIf { it.isNotBlank() }
    }

    private companion object {
        /** `invalid_state: …` → `…`: el código es para los logs, el operador lee la explicación. */
        val CODE_PREFIX = Regex("^[a-z_]+:\\s*")
    }
}
