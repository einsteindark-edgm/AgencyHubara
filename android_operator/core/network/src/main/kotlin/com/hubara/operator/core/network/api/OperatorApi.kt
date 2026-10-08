package com.hubara.operator.core.network.api

import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.HandoffResponseDto
import com.hubara.operator.core.network.dto.HotDto
import com.hubara.operator.core.network.dto.HumanDto
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
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/** Las rutas que usa la app. Las que ya existen son las del dashboard; las nuevas, las de `/api/chats/mobile`. */
interface OperatorApi {
    @GET("api/dashboard/sessions")
    suspend fun sessions(): SessionsResponse

    @GET("api/dashboard/sessions/{id}")
    suspend fun session(@Path("id") id: String): SessionDetailsDto

    @POST("api/dashboard/sessions/{id}/intervene")
    suspend fun intervene(@Path("id") id: String, @Body body: InterveneRequest): HandoffResponseDto

    @POST("api/dashboard/sessions/{id}/return-to-bot")
    suspend fun returnToBot(@Path("id") id: String, @Body body: ReturnToBotRequest): HandoffResponseDto

    @POST("api/dashboard/sessions/{id}/messages")
    suspend fun sendMessage(@Path("id") id: String, @Body body: HumanMessageRequest): Response<HumanMessageResponseDto>

    @GET("api/dashboard/whatsapp-templates")
    suspend fun templates(): com.hubara.operator.core.network.dto.TemplatesDto

    @POST("api/dashboard/sessions/{id}/template-messages")
    suspend fun sendTemplate(@Path("id") id: String, @Body body: com.hubara.operator.core.network.dto.TemplateMessageRequest): Response<HumanMessageResponseDto>

    @POST("api/dashboard/sse-ticket")
    suspend fun sseTicket(): SseTicketDto

    @GET("api/chats/mobile/suggestions/{id}")
    suspend fun suggestions(@Path("id") id: String): SuggestionsDto

    @POST("api/chats/session-actions/{key}/tools/{tool}")
    suspend fun runTool(@Path("key") key: String, @Path("tool") tool: String, @Body body: ToolRequest): Response<ToolResponseDto>

    @GET("api/chats/mobile/fires")
    suspend fun fires(): FiresDto

    @GET("api/chats/mobile/hot")
    suspend fun hot(): HotDto

    /** Los chats que atiende una persona (página «Humano» del widget). */
    @GET("api/chats/mobile/human")
    suspend fun human(): HumanDto

    @GET("api/orders/orders")
    suspend fun orders(@Query("limit") limit: Int = 50): OrderListDto

    @GET("api/orders/orders/{id}")
    suspend fun order(@Path("id") id: String): OrderDetailDto

    @PATCH("api/orders/orders/{id}/stage")
    suspend fun transitionStage(@Path("id") id: String, @Body body: StageRequest): OrderCommandResultDto

    @GET("api/dashboard/media/{session}/{file}")
    suspend fun media(@Path("session") session: String, @Path("file") file: String): ResponseBody

    /** Si el servidor tiene Firebase y con qué opciones lo arranca el teléfono. */
    @GET("api/chats/mobile/push")
    suspend fun pushConfig(): PushConfigDto

    @POST("api/chats/mobile/devices")
    suspend fun registerDevice(@Body body: DeviceRequest): Response<Unit>

    @DELETE("api/chats/mobile/devices/{token}")
    suspend fun unregisterDevice(@Path("token") token: String): Response<Unit>
}
