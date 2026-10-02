package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.model.OrderDetail
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.OrderSummary
import com.hubara.operator.core.network.api.CommandResult
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Órdenes: la lista vive en memoria (la fuente es OrderFacts en el servidor) y el detalle se pide al abrir. */
@Singleton
class OrderRepository @Inject constructor(private val service: OperatorService) {
    private val _orders = MutableStateFlow<List<OrderSummary>>(emptyList())
    val orders: StateFlow<List<OrderSummary>> = _orders.asStateFlow()

    suspend fun refreshList(): Result<Unit> = safeCall {
        _orders.value = service.api.orders().orders.mapNotNull { it.toDomain() }
    }

    suspend fun detail(id: OrderId): Result<OrderDetail> = safeCall {
        service.api.order(id.raw).toDomain() ?: error("orden inválida")
    }

    suspend fun advance(id: OrderId, to: OrderStage, trackingUrl: String? = null, shippingCostCop: Long? = null): CommandResult =
        service.advanceStage(id, to, trackingUrl, shippingCostCop)
}
