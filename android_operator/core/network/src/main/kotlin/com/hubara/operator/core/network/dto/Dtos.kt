package com.hubara.operator.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

// ── Bandeja e historial (`/api/dashboard/sessions`) ───────────────────────────────────────────

@Serializable
data class SessionsResponse(val sessions: List<ChatSessionDto> = emptyList())

@Serializable
data class OrderRefDto(
    @SerialName("order_id") val orderId: String,
    @SerialName("display_id") val displayId: String? = null,
    val payment: String = "pending",
    val count: Int = 1,
)

@Serializable
data class ChatSessionDto(
    @SerialName("session_id") val sessionId: String,
    @SerialName("phone_number") val phoneNumber: String = "",
    val tag: String = "",
    @SerialName("active_agent_route") val activeAgentRoute: String = "",
    @SerialName("order_ref") val orderRef: OrderRefDto? = null,
    /** mtime del historial, en SEGUNDOS (float). */
    @SerialName("last_updated_timestamp") val lastUpdatedTimestamp: Double = 0.0,
    @SerialName("last_inbound_ms") val lastInboundMs: Long? = null,
    /** Total de mensajes del cliente (#384); los no leídos los calcula la app con lo que ya vio. */
    @SerialName("inbound_count") val inboundCount: Int = 0,
    /** Nombre de perfil de WhatsApp (opcional: sin él la app muestra el número). */
    @SerialName("customer_name") val customerName: String? = null,
    /** Lo último con texto del historial, ya recortado por el backend. */
    @SerialName("last_message_preview") val lastMessagePreview: String? = null,
)

@Serializable
data class ChatMessageDto(
    @SerialName("ui_type") val uiType: String,
    val role: String = "",
    val content: String? = null,
    val sender: String? = null,
    /** ISO-8601 (lo normal) o número. */
    val timestamp: JsonElement? = null,
    @SerialName("image_url") val imageUrl: String? = null,
    val wamid: String? = null,
)

@Serializable
data class SessionDetailsDto(
    @SerialName("session_id") val sessionId: String,
    @SerialName("phone_number") val phoneNumber: String = "",
    val tag: String = "",
    @SerialName("active_agent_route") val activeAgentRoute: String = "",
    @SerialName("service_window_expires_at_ms") val serviceWindowExpiresAtMs: Long? = null,
    @SerialName("order_ref") val orderRef: OrderRefDto? = null,
    val messages: List<ChatMessageDto> = emptyList(),
)

// ── Handoff (`/api/dashboard/sessions/{id}/…`) ─────────────────────────────────────────────────

@Serializable
data class InterveneRequest(val motivo: String? = null)

@Serializable
data class ReturnToBotRequest(@SerialName("target_route") val targetRoute: String = "ventas", val motivo: String? = null)

@Serializable
data class HandoffResponseDto(
    val ok: Boolean,
    @SerialName("active_route") val activeRoute: String,
    val tag: String = "",
)

@Serializable
data class HumanMessageRequest(
    val text: String? = null,
    @SerialName("attachment_id") val attachmentId: String? = null,
    @SerialName("client_message_id") val clientMessageId: String,
)

@Serializable
data class HumanMessageResponseDto(val ok: Boolean, val content: String = "")

@Serializable
data class SseTicketDto(val ticket: String)

@Serializable
data class TemplateVariableDto(val name: String, val description: String? = null, @SerialName("max_length") val maxLength: Int? = null)

@Serializable
data class TemplateDto(
    val name: String,
    val body: String? = null,
    val variables: List<TemplateVariableDto> = emptyList(),
    @SerialName("is_default") val isDefault: Boolean = false,
    @SerialName("header_format") val headerFormat: String? = null,
)

@Serializable
data class TemplatesDto(val templates: List<TemplateDto> = emptyList())

@Serializable
data class TemplateMessageRequest(
    @SerialName("template_name") val templateName: String,
    val variables: Map<String, String> = emptyMap(),
    @SerialName("client_message_id") val clientMessageId: String,
)

// ── Burbujas y acciones del operador ───────────────────────────────────────────────────────────

@Serializable
data class ActionRefDto(val name: String, val args: JsonObject = JsonObject(emptyMap()))

@Serializable
data class SuggestionDto(
    val id: String,
    val label: String,
    val prominence: String = "normal",
    val editable: Boolean = false,
    val action: ActionRefDto,
)

@Serializable
data class SuggestionsDto(
    @SerialName("session_id") val sessionId: String,
    val version: Long = 0,
    @SerialName("decided_by") val decidedBy: String = "rules",
    val stage: String? = null,
    @SerialName("window_open") val windowOpen: Boolean = true,
    @SerialName("in_control") val inControl: String = "bot",
    val suggestions: List<SuggestionDto> = emptyList(),
)

@Serializable
data class ToolRequest(
    @SerialName("client_action_id") val clientActionId: String,
    val args: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class ToolResponseDto(
    val sent: Boolean = false,
    val deduplicated: Boolean = false,
    val error: String? = null,
)

// ── Incendios y ventas calientes ───────────────────────────────────────────────────────────────

@Serializable
data class FireSubjectDto(
    val kind: String,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("order_id") val orderId: String? = null,
)

@Serializable
data class FireDto(
    @SerialName("fire_id") val fireId: String,
    val subject: FireSubjectDto,
    val severity: String,
    val kind: String = "other",
    @SerialName("getting_worse") val gettingWorse: Boolean = false,
    val title: String = "",
    val subtitle: String = "",
    @SerialName("primary_action") val primaryAction: ActionRefDto = ActionRefDto("open_chat"),
    @SerialName("updated_ms") val updatedMs: Long = 0,
)

@Serializable
data class FiresDto(
    @SerialName("decided_by") val decidedBy: String = "rules",
    val fires: List<FireDto> = emptyList(),
)

@Serializable
data class HotSaleDto(
    @SerialName("session_id") val sessionId: String,
    val name: String? = null,
    val stage: String? = null,
    val product: String? = null,
    @SerialName("cart_value_cop") val cartValueCop: Long? = null,
    val risk: Boolean = false,
    @SerialName("updated_ms") val updatedMs: Long = 0,
)

@Serializable
data class HotDto(val hot: List<HotSaleDto> = emptyList())

/** Una conversación que atiende una persona (`GET /api/chats/mobile/human`): sin mensajes, va a la pantalla de inicio. */
@Serializable
data class HumanChatDto(
    @SerialName("session_id") val sessionId: String,
    val name: String? = null,
    /** Mensajes del cliente sin respuesta (0 = al día). */
    val unanswered: Int = 0,
    @SerialName("waiting_since_ms") val waitingSinceMs: Long? = null,
    @SerialName("last_inbound_ms") val lastInboundMs: Long? = null,
)

@Serializable
data class HumanDto(val total: Int = 0, val human: List<HumanChatDto> = emptyList())

// ── Órdenes (`/api/orders/orders`) ─────────────────────────────────────────────────────────────

@Serializable
data class OrderSummaryDto(
    val id: String,
    @SerialName("display_id") val displayId: String = "",
    val customer: String = "",
    val city: String? = null,
    val status: String = "new",
    @SerialName("pay_status") val payStatus: String = "pending",
    @SerialName("total_cop") val totalCop: Long = 0,
    val overdue: Boolean = false,
    @SerialName("updated_at_ms") val updatedAtMs: Long = 0,
)

@Serializable
data class OrderListDto(val orders: List<OrderSummaryDto> = emptyList())

@Serializable
data class OrderItemDto(
    val title: String,
    val quantity: Int = 1,
    @SerialName("unit_price_cop") val unitPriceCop: Long = 0,
    @SerialName("total_cop") val totalCop: Long = 0,
    @SerialName("variant_label") val variantLabel: String? = null,
    val thumbnail: String? = null,
)

@Serializable
data class OrderAddressDto(
    @SerialName("first_name") val firstName: String? = null,
    @SerialName("last_name") val lastName: String? = null,
    val phone: String? = null,
    @SerialName("address_1") val address1: String? = null,
    @SerialName("address_2") val address2: String? = null,
    val city: String? = null,
)

@Serializable
data class OrderDetailDto(
    val summary: OrderSummaryDto,
    @SerialName("items_detail") val itemsDetail: List<OrderItemDto> = emptyList(),
    @SerialName("shipping_address") val shippingAddress: OrderAddressDto? = null,
    @SerialName("subtotal_cop") val subtotalCop: Long = 0,
    @SerialName("shipping_cop") val shippingCop: Long = 0,
    @SerialName("discount_total_cop") val discountTotalCop: Long = 0,
    @SerialName("payment_method_label") val paymentMethodLabel: String? = null,
    @SerialName("data_completeness_missing") val dataCompletenessMissing: List<String> = emptyList(),
)

@Serializable
data class StageRequest(
    val stage: String,
    val note: String? = null,
    val force: Boolean = false,
    @SerialName("tracking_url") val trackingUrl: String? = null,
    @SerialName("shipping_cost") val shippingCost: Long? = null,
)

@Serializable
data class OrderCommandResultDto(
    val success: Boolean,
    @SerialName("order_id") val orderId: String = "",
    @SerialName("current_stage") val currentStage: String? = null,
    @SerialName("error_detail") val errorDetail: String? = null,
)

// ── SSE del dashboard ──────────────────────────────────────────────────────────────────────────

@Serializable
data class DashboardEventDto(
    val domain: String,
    val type: String,
    val id: String? = null,
    val payload: JsonElement? = null,
    @SerialName("ts_ms") val tsMs: Long = 0,
)

// ── Avisos push (`/api/chats/mobile/push`, `/api/chats/mobile/devices`) ─────────────────────────────

/** Lo que el teléfono necesita para hablar con Firebase. Sale de SSM (nunca del APK): el repo es público. */
@Serializable
data class FirebaseOptionsDto(
    @SerialName("project_id") val projectId: String,
    @SerialName("application_id") val applicationId: String,
    @SerialName("api_key") val apiKey: String,
    @SerialName("gcm_sender_id") val gcmSenderId: String,
)

@Serializable
data class PushConfigDto(val enabled: Boolean = false, val firebase: FirebaseOptionsDto? = null)

@Serializable
data class DeviceRequest(
    val token: String,
    val platform: String,
    @SerialName("app_version") val appVersion: String,
)
