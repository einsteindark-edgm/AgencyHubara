package com.hubara.operator.core.model

/** Etapas de la orden, en el orden del stepper. */
enum class OrderStage {
    NEW, PREPARING, READY, SHIPPING, DELIVERED, CANCELLED;

    /** El paso siguiente permitido, o null si no hay (entregada o cancelada). */
    fun next(): OrderStage? = when (this) {
        NEW -> PREPARING
        PREPARING -> READY
        READY -> SHIPPING
        SHIPPING -> DELIVERED
        DELIVERED, CANCELLED -> null
    }

    /** Qué datos pide avanzar A esta etapa. */
    val requires: Set<StageInput>
        get() = when (this) {
            READY -> setOf(StageInput.PHOTO)
            SHIPPING -> setOf(StageInput.TRACKING_URL, StageInput.SHIPPING_COST)
            else -> emptySet()
        }

    companion object {
        fun fromBackend(raw: String): OrderStage? = when (raw) {
            "new" -> NEW
            "preparing" -> PREPARING
            "ready" -> READY
            "shipping" -> SHIPPING
            "delivered" -> DELIVERED
            "cancelled" -> CANCELLED
            else -> null
        }
    }
}

enum class StageInput { PHOTO, TRACKING_URL, SHIPPING_COST }

enum class PayStatus { PAID, PARTIAL, PENDING, REFUND }

data class OrderSummary(
    val id: OrderId,
    val displayId: String,
    val customer: String,
    val city: String?,
    val stage: OrderStage,
    val payStatus: PayStatus,
    val totalCop: Long,
    val overdue: Boolean,
    val updatedAtMs: Long,
)

data class OrderItem(
    val title: String,
    val variant: String?,
    val quantity: Int,
    val unitPriceCop: Long,
    val totalCop: Long,
    val thumbnailUrl: String?,
)

data class ShippingAddress(
    val receiver: String?,
    val phone: String?,
    val address: String?,
    val neighborhood: String?,
    val city: String?,
)

data class OrderDetail(
    val summary: OrderSummary,
    val items: List<OrderItem>,
    val address: ShippingAddress?,
    val subtotalCop: Long,
    val shippingCop: Long,
    val discountCop: Long,
    val paymentLabel: String?,
    val missing: List<String>,
)
