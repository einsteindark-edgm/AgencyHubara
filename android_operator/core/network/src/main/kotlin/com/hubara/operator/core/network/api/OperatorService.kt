package com.hubara.operator.core.network.api

import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.dto.StageRequest
import com.hubara.operator.core.network.dto.ToolRequest
import java.io.IOException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Resultado de ejecutar una acción del bot como humano. */
sealed interface ToolResult {
    data class Sent(val deduplicated: Boolean) : ToolResult
    data object WindowClosed : ToolResult
    data object NotInControl : ToolResult
    data class Rejected(val code: Int, val detail: String) : ToolResult
    /** Red o 5xx: el outbox reintenta. */
    data object Transient : ToolResult
}

sealed interface CommandResult {
    data class Ok(val stage: OrderStage?) : CommandResult
    data class Failed(val detail: String) : CommandResult
}

/** Envoltorio de [OperatorApi] con resultados tipados para lo que tiene reglas de reintento. */
class OperatorService(val api: OperatorApi) {

    suspend fun runTool(session: SessionId, action: ActionRef, clientActionId: String): ToolResult = try {
        val response = api.runTool(session.raw, action.name, ToolRequest(clientActionId, action.args))
        when {
            response.isSuccessful -> ToolResult.Sent(deduplicated = response.body()?.deduplicated == true)
            response.code() == 409 -> when (errorCode(response.errorBody()?.string())) {
                "window_closed" -> ToolResult.WindowClosed
                "not_in_control" -> ToolResult.NotInControl
                else -> ToolResult.Rejected(409, "conflict")
            }
            response.code() >= 500 || response.code() == 429 -> ToolResult.Transient
            else -> ToolResult.Rejected(response.code(), rejectionMessage(response.errorBody()?.string()))
        }
    } catch (_: IOException) {
        ToolResult.Transient
    }

    suspend fun advanceStage(
        order: OrderId,
        to: OrderStage,
        trackingUrl: String? = null,
        shippingCostCop: Long? = null,
        note: String? = null,
    ): CommandResult = try {
        val r = api.transitionStage(
            order.raw,
            StageRequest(stage = to.name.lowercase(), note = note, trackingUrl = trackingUrl, shippingCost = shippingCostCop),
        )
        if (r.success) CommandResult.Ok(r.currentStage?.let(OrderStage::fromBackend))
        else CommandResult.Failed(r.errorDetail ?: "error")
    } catch (e: HttpException) {
        CommandResult.Failed("http_${e.code()}")
    } catch (_: IOException) {
        CommandResult.Failed("network")
    }

    /** 422 del backend: `tool_rejected` trae el mensaje de la tool del bot; `invalid_args`, la lista de problemas. */
    private fun rejectionMessage(body: String?): String {
        val json = runCatching { OperatorJson.parseToJsonElement(body.orEmpty()).jsonObject }.getOrNull() ?: return "rechazado"
        json["message"]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() }?.let { return it.take(200) }
        return when (json["error"]?.jsonPrimitive?.contentOrNull) {
            "invalid_args" -> "faltan datos para esta acción"
            "unknown_tool" -> "la app pidió una acción que el servidor no conoce"
            "session_not_found" -> "la conversación no existe"
            else -> "rechazado"
        }
    }

    private fun errorCode(body: String?): String? {
        val json = runCatching { OperatorJson.parseToJsonElement(body.orEmpty()).jsonObject }.getOrNull() ?: return null
        json["error"]?.jsonPrimitive?.contentOrNull?.let { return it }
        return (json["detail"] as? JsonObject)?.get("error")?.jsonPrimitive?.contentOrNull
    }

    companion object {
        fun retrofit(client: OkHttpClient, baseUrl: HttpUrl): Retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(OperatorJson.asConverterFactory("application/json".toMediaType()))
            .build()

        fun create(client: OkHttpClient, baseUrl: HttpUrl): OperatorService =
            OperatorService(retrofit(client, baseUrl).create(OperatorApi::class.java))
    }
}
