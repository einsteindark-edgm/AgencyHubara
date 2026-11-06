package com.hubara.operator.core.data

import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.HandoffResponseDto
import com.hubara.operator.core.network.dto.HotDto
import com.hubara.operator.core.network.dto.HumanDto
import com.hubara.operator.core.network.dto.HumanMessageRequest
import com.hubara.operator.core.network.dto.HumanMessageResponseDto
import com.hubara.operator.core.network.dto.InterveneRequest
import com.hubara.operator.core.network.dto.MediaUploadDto
import com.hubara.operator.core.network.dto.OrderCommandResultDto
import com.hubara.operator.core.network.dto.OrderDetailDto
import com.hubara.operator.core.network.dto.OrderListDto
import com.hubara.operator.core.network.dto.PushConfigDto
import com.hubara.operator.core.network.dto.ReturnToBotRequest
import com.hubara.operator.core.network.dto.SessionDetailsDto
import com.hubara.operator.core.network.dto.SessionsResponse
import com.hubara.operator.core.network.dto.SseTicketDto
import com.hubara.operator.core.network.dto.StageRequest
import com.hubara.operator.core.network.dto.SuggestionsDto
import com.hubara.operator.core.network.dto.ToolRequest
import com.hubara.operator.core.network.dto.ToolResponseDto
import java.io.IOException
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.ResponseBody
import okhttp3.ResponseBody.Companion.toResponseBody
import retrofit2.Response

/** API en memoria: cada test pone las respuestas que necesita y cuenta las llamadas. */
class FakeOperatorApi : OperatorApi {
    var sessionsResponse = SessionsResponse()
    val details = mutableMapOf<String, SessionDetailsDto>()
    var suggestionsResponse: (String) -> SuggestionsDto = { SuggestionsDto(sessionId = it) }
    var firesResponse = FiresDto()
    var sendMessageCode = 200
    var sendMessageBody = "{}"
    var uploadCode = 200
    var uploadBody = "{}"
    var uploadError: IOException? = null
    val uploads = mutableListOf<Upload>()

    /** Una foto que llegó al backend: la cabecera del campo, su tipo y los bytes. */
    class Upload(val session: String, val disposition: String?, val mime: String?, val bytes: ByteArray)
    var runToolCode = 200
    var runToolBody = """{"error":"window_closed"}"""
    val calls = mutableListOf<String>()
    val sentMessages = mutableListOf<HumanMessageRequest>()
    val toolRequests = mutableListOf<Pair<String, ToolRequest>>()

    /** Si no es null, pedir la bandeja falla con esto (sin red). */
    var sessionsError: Exception? = null

    override suspend fun sessions(): SessionsResponse {
        calls += "sessions"
        sessionsError?.let { throw it }
        return sessionsResponse
    }
    override suspend fun session(id: String) = (details[id] ?: SessionDetailsDto(sessionId = id)).also { calls += "session:$id" }
    override suspend fun intervene(id: String, body: InterveneRequest) = HandoffResponseDto(true, "humano").also { calls += "intervene:$id" }
    override suspend fun returnToBot(id: String, body: ReturnToBotRequest) = HandoffResponseDto(true, "ventas").also { calls += "return:$id" }

    override suspend fun sendMessage(id: String, body: HumanMessageRequest): Response<HumanMessageResponseDto> {
        calls += "send:$id"
        sentMessages += body
        return if (sendMessageCode in 200..299) Response.success(HumanMessageResponseDto(true, body.text.orEmpty()))
        else Response.error(sendMessageCode, sendMessageBody.toResponseBody(JSON))
    }

    override suspend fun uploadMedia(id: String, file: MultipartBody.Part): Response<MediaUploadDto> {
        calls += "upload:$id"
        uploadError?.let { throw it }
        val bytes = okio.Buffer().also { file.body.writeTo(it) }.readByteArray()
        uploads += Upload(id, file.headers?.get("Content-Disposition"), file.body.contentType()?.toString(), bytes)
        val n = uploads.size
        return if (uploadCode in 200..299) Response.success(MediaUploadDto(true, "media-$n", "/api/dashboard/media/$id/out-$n.jpg"))
        else Response.error(uploadCode, uploadBody.toResponseBody(JSON))
    }

    var templatesResponse = com.hubara.operator.core.network.dto.TemplatesDto()
    val sentTemplates = mutableListOf<com.hubara.operator.core.network.dto.TemplateMessageRequest>()
    override suspend fun templates() = templatesResponse
    override suspend fun sendTemplate(id: String, body: com.hubara.operator.core.network.dto.TemplateMessageRequest): Response<HumanMessageResponseDto> {
        calls += "template:$id"
        sentTemplates += body
        return Response.success(HumanMessageResponseDto(true, ""))
    }
    override suspend fun sseTicket() = SseTicketDto("t").also { calls += "sse-ticket" }
    val suggestionFeatures = mutableListOf<String>()
    override suspend fun suggestions(id: String, features: String) =
        suggestionsResponse(id).also { calls += "suggestions:$id"; suggestionFeatures += features }

    override suspend fun runTool(key: String, tool: String, body: ToolRequest): Response<ToolResponseDto> {
        calls += "tool:$key:$tool"
        toolRequests += tool to body
        return if (runToolCode in 200..299) Response.success(ToolResponseDto(sent = true))
        else Response.error(runToolCode, runToolBody.toResponseBody(JSON))
    }

    /** Si no es null, pedir los incendios espera a que el test lo suelte (la red tarda). */
    var firesGate: kotlinx.coroutines.CompletableDeferred<Unit>? = null

    override suspend fun fires(): FiresDto {
        calls += "fires"
        firesGate?.await()
        return firesResponse
    }
    override suspend fun hot() = HotDto()
    override suspend fun human() = HumanDto()
    override suspend fun orders(limit: Int) = OrderListDto().also { calls += "orders" }
    override suspend fun order(id: String): OrderDetailDto = error("no usado")
    override suspend fun transitionStage(id: String, body: StageRequest) = OrderCommandResultDto(true, id, body.stage)
    override suspend fun media(session: String, file: String): ResponseBody = "".toResponseBody(JSON)
    override suspend fun pushConfig() = PushConfigDto()
    override suspend fun registerDevice(body: DeviceRequest): Response<Unit> = Response.success(Unit)
    override suspend fun unregisterDevice(token: String): Response<Unit> = Response.success(Unit)

    private companion object { val JSON = "application/json".toMediaType() }
}

class FakeScheduler : com.hubara.operator.core.data.outbox.OutboxScheduler {
    val enqueued = mutableListOf<Pair<String, Long>>()
    val cancelled = mutableListOf<String>()
    override fun enqueue(id: String, delayMs: Long) { enqueued += id to delayMs }
    override fun cancel(id: String) { cancelled += id }
}

/** Prepara las fotos sin decodificar nada: cada `source` conocido da su archivo; uno desconocido no se puede leer. */
class FakePhotoPreparer(private val files: Map<String, java.io.File> = emptyMap()) :
    com.hubara.operator.core.data.outbox.PhotoPreparer {
    var cleared = 0
    override suspend fun prepare(source: String): java.io.File? = files[source]
    override suspend fun clear() { cleared++ }
}
