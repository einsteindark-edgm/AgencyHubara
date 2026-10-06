package com.hubara.operator.core.data

import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.HandoffResponseDto
import com.hubara.operator.core.network.dto.HotDto
import com.hubara.operator.core.network.dto.HumanMessageRequest
import com.hubara.operator.core.network.dto.HumanMessageResponseDto
import com.hubara.operator.core.network.dto.InterveneRequest
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
import okhttp3.MediaType.Companion.toMediaType
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
    var runToolCode = 200
    var runToolBody = """{"error":"window_closed"}"""
    val calls = mutableListOf<String>()
    val sentMessages = mutableListOf<HumanMessageRequest>()
    val toolRequests = mutableListOf<Pair<String, ToolRequest>>()

    override suspend fun sessions() = sessionsResponse.also { calls += "sessions" }
    override suspend fun session(id: String) = (details[id] ?: SessionDetailsDto(sessionId = id)).also { calls += "session:$id" }
    override suspend fun intervene(id: String, body: InterveneRequest) = HandoffResponseDto(true, "humano").also { calls += "intervene:$id" }
    override suspend fun returnToBot(id: String, body: ReturnToBotRequest) = HandoffResponseDto(true, "ventas").also { calls += "return:$id" }

    override suspend fun sendMessage(id: String, body: HumanMessageRequest): Response<HumanMessageResponseDto> {
        calls += "send:$id"
        sentMessages += body
        return if (sendMessageCode in 200..299) Response.success(HumanMessageResponseDto(true, body.text.orEmpty()))
        else Response.error(sendMessageCode, "{}".toResponseBody(JSON))
    }

    var templatesResponse = com.hubara.operator.core.network.dto.TemplatesDto()
    val sentTemplates = mutableListOf<com.hubara.operator.core.network.dto.TemplateMessageRequest>()
    override suspend fun templates() = templatesResponse
    override suspend fun sendTemplate(id: String, body: com.hubara.operator.core.network.dto.TemplateMessageRequest): Response<HumanMessageResponseDto> {
        calls += "template:$id"
        sentTemplates += body
        return Response.success(HumanMessageResponseDto(true, ""))
    }
    override suspend fun sseTicket() = SseTicketDto("t")
    override suspend fun suggestions(id: String) = suggestionsResponse(id).also { calls += "suggestions:$id" }

    override suspend fun runTool(key: String, tool: String, body: ToolRequest): Response<ToolResponseDto> {
        calls += "tool:$key:$tool"
        toolRequests += tool to body
        return if (runToolCode in 200..299) Response.success(ToolResponseDto(sent = true))
        else Response.error(runToolCode, runToolBody.toResponseBody(JSON))
    }

    override suspend fun fires() = firesResponse.also { calls += "fires" }
    override suspend fun hot() = HotDto()
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
