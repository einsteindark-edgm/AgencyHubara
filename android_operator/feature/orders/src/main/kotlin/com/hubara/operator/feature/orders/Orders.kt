package com.hubara.operator.feature.orders

import com.hubara.operator.core.ui.RadarIndicator
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.adaptive.ExperimentalMaterial3AdaptiveApi
import androidx.compose.material3.adaptive.navigation3.ListDetailSceneStrategy
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.repo.OrderRepository
import com.hubara.operator.core.model.OrderDetail
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.OrderSummary
import com.hubara.operator.core.model.PayStatus
import com.hubara.operator.core.model.StageInput
import com.hubara.operator.core.navigation.BottomSheetSceneStrategy
import com.hubara.operator.core.navigation.EntryProviderInstaller
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.navigation.OrdersKey
import com.hubara.operator.core.network.api.CommandResult
import com.hubara.operator.core.ui.OrderStepper
import com.hubara.operator.core.ui.stageLabel
import dagger.Module
import dagger.Provides
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ActivityRetainedComponent
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.multibindings.IntoSet
import java.text.NumberFormat
import java.util.Locale
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private val COP = NumberFormat.getIntegerInstance(Locale.forLanguageTag("es-CO"))

fun cop(value: Long): String = "$" + COP.format(value).replace(',', '.').replace(' ', '.')

fun parseCop(raw: String): Long? = raw.filter(Char::isDigit).toLongOrNull()?.takeIf { it > 0 }

fun advanceLabel(current: OrderStage): String? = when (current.next()) {
    OrderStage.PREPARING -> "Empezar a preparar"
    OrderStage.READY -> "Marcar listo"
    OrderStage.SHIPPING -> "Despachar"
    OrderStage.DELIVERED -> "Marcar entregado"
    else -> null
}

// ── Lista ─────────────────────────────────────────────────────────────────────────────────────

@HiltViewModel
class OrdersViewModel @Inject constructor(private val repo: OrderRepository) : ViewModel() {
    val orders: StateFlow<List<OrderSummary>> = repo.orders
    init { viewModelScope.launch { repo.refreshList() } }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen(vm: OrdersViewModel, onOpen: (OrderId) -> Unit) {
    val orders by vm.orders.collectAsStateWithLifecycle()
    Scaffold(topBar = { TopAppBar(title = { Text("Órdenes") }, actions = { RadarIndicator() }) }) { inner ->
        LazyColumn(Modifier.fillMaxSize(), contentPadding = inner) {
            items(orders, key = { it.id.raw }) { o ->
                Row(
                    Modifier.fillMaxWidth().clickable(role = Role.Button) { onOpen(o.id) }.padding(horizontal = 16.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("#${o.displayId} · ${o.customer}", style = MaterialTheme.typography.titleSmall)
                        val late = if (o.overdue) " · atrasada" else ""
                        Text("${stageLabel(o.stage)}$late · ${o.city.orEmpty()}", style = MaterialTheme.typography.bodySmall,
                            color = if (o.overdue) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Text(cop(o.totalCop), style = MaterialTheme.typography.bodyMedium)
                }
                HorizontalDivider()
            }
        }
    }
}

// ── Ficha ─────────────────────────────────────────────────────────────────────────────────────

data class OrderSheetState(
    val loading: Boolean = true,
    val detail: OrderDetail? = null,
    val busy: Boolean = false,
    val message: String? = null,
)

@HiltViewModel(assistedFactory = OrderSheetViewModel.Factory::class)
class OrderSheetViewModel @AssistedInject constructor(
    @Assisted orderRaw: String,
    private val repo: OrderRepository,
) : ViewModel() {
    private val orderId = requireNotNull(OrderId.parse(orderRaw))
    private val _state = MutableStateFlow(OrderSheetState())
    val state: StateFlow<OrderSheetState> = _state.asStateFlow()

    init { load() }

    fun load() {
        viewModelScope.launch {
            repo.detail(orderId)
                .onSuccess { _state.value = OrderSheetState(loading = false, detail = it) }
                .onFailure { _state.value = OrderSheetState(loading = false, message = "No se pudo cargar la orden.") }
        }
    }

    fun advance(trackingUrl: String?, shippingCost: Long?) {
        val detail = _state.value.detail ?: return
        val next = detail.summary.stage.next() ?: return
        viewModelScope.launch {
            _state.value = _state.value.copy(busy = true, message = null)
            when (val r = repo.advance(orderId, next, trackingUrl, shippingCost)) {
                is CommandResult.Ok -> load()
                is CommandResult.Failed -> _state.value = _state.value.copy(busy = false, message = "No se pudo avanzar: ${r.detail}")
            }
        }
    }

    @AssistedFactory
    interface Factory { fun create(orderRaw: String): OrderSheetViewModel }
}

@Composable
fun OrderSheet(vm: OrderSheetViewModel) {
    val ui by vm.state.collectAsStateWithLifecycle()
    Column(
        Modifier.fillMaxWidth().navigationBarsPadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        val d = ui.detail
        when {
            ui.loading -> CircularProgressIndicator()
            d == null -> Text(ui.message ?: "Sin datos.")
            else -> OrderSheetContent(d, ui, vm)
        }
    }
}

@Composable
private fun OrderSheetContent(d: OrderDetail, ui: OrderSheetState, vm: OrderSheetViewModel) {
    val s = d.summary
    Text("Pedido #${s.displayId} · ${s.customer}", style = MaterialTheme.typography.titleMedium)
    Section("QUÉ COMPRÓ")
    d.items.forEach { item ->
        Row(Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f)) {
                Text("${item.title} × ${item.quantity}", style = MaterialTheme.typography.bodyMedium)
                item.variant?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            Text(cop(item.totalCop), style = MaterialTheme.typography.bodyMedium)
        }
    }
    Section("ESTADO Y SIGUIENTE PASO")
    OrderStepper(s.stage)
    NextStep(s.stage, ui.busy, onAdvance = vm::advance)
    ui.message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    Section("PAGO Y ENVÍO")
    val pago = when (s.payStatus) {
        PayStatus.PAID -> "pagado ✓"
        PayStatus.PARTIAL -> "pago parcial"
        PayStatus.REFUND -> "reembolso"
        PayStatus.PENDING -> "pago pendiente"
    }
    Field("PAGO", listOfNotNull(d.paymentLabel, pago).joinToString(" · "))
    Field("TOTAL", "${cop(s.totalCop)} (envío ${cop(d.shippingCop)})")
    d.address?.let { a ->
        a.receiver?.let { Field("RECIBE", it) }
        a.address?.let { Field("DIRECCIÓN", it) }
        listOfNotNull(a.neighborhood, a.city).joinToString(" · ").takeIf { it.isNotBlank() }?.let { Field("BARRIO", it) }
    }
}

@Composable
private fun NextStep(current: OrderStage, busy: Boolean, onAdvance: (String?, Long?) -> Unit) {
    val label = advanceLabel(current) ?: return
    val next = current.next() ?: return
    var tracking by rememberSaveable { mutableStateOf("") }
    var cost by rememberSaveable { mutableStateOf("") }
    when {
        StageInput.PHOTO in next.requires ->
            // La foto de «listo» se sube con la cámara; esa pantalla todavía no está en la app.
            Text("Para marcarla lista hace falta la foto del pedido: por ahora súbela desde el dashboard.",
                style = MaterialTheme.typography.bodySmall)
        StageInput.TRACKING_URL in next.requires -> {
            OutlinedTextField(tracking, { tracking = it }, label = { Text("Link de la guía") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(cost, { cost = it }, label = { Text("Costo real del envío") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
            val ok = tracking.startsWith("http") && parseCop(cost) != null
            Button(onClick = { onAdvance(tracking.trim(), parseCop(cost)) }, enabled = ok && !busy, modifier = Modifier.fillMaxWidth()) { Text(label) }
        }
        else -> Button(onClick = { onAdvance(null, null) }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text(label) }
    }
}

@Composable
private fun Section(title: String) {
    Text(title, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = 4.dp))
}

@Composable
private fun Field(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 2.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Module
@InstallIn(ActivityRetainedComponent::class)
object OrdersNavigation {
    @OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        entry<OrdersKey>(metadata = ListDetailSceneStrategy.listPane()) {
            OrdersScreen(hiltViewModel(), onOpen = { navigator.navigate(OrderSheetKey(it)) })
        }
        entry<OrderSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { key ->
            OrderSheet(hiltViewModel<OrderSheetViewModel, OrderSheetViewModel.Factory>(creationCallback = { it.create(key.order.raw) }))
        }
    }
}
