@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.orders

import com.hubara.operator.core.ui.RadarIndicator
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
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
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.collections.immutable.toImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.ImmutableList
import com.hubara.operator.core.ui.listContent
import com.hubara.operator.core.ui.ListContent
import com.hubara.operator.core.designsystem.StatusPill
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.EmptyState
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.Alignment
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Icon
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Box
import com.hubara.operator.core.navigation.SceneKeys
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LoadingIndicator

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

data class OrdersUiState(
    val orders: ImmutableList<OrderSummary> = persistentListOf(),
    val content: ListContent = ListContent.LOADING,
)

@HiltViewModel
class OrdersViewModel @Inject constructor(private val repo: OrderRepository) : ViewModel() {
    /** null = la primera recarga no ha vuelto; si no, si falló. */
    private val refresh = MutableStateFlow<Boolean?>(null)

    val state: StateFlow<OrdersUiState> = combine(repo.orders, refresh) { orders, failed ->
        OrdersUiState(orders.toImmutableList(), listContent(orders.isEmpty(), refreshed = failed != null, failed = failed == true))
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), OrdersUiState())

    init { viewModelScope.launch { refresh.value = repo.refreshList().isFailure } }
}

@Composable
fun OrdersRoute(vm: OrdersViewModel, onOpen: (OrderId) -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    OrdersScreen(ui, onOpen)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen(ui: OrdersUiState, onOpen: (OrderId) -> Unit) {
    val scroll = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            TopAppBar(title = { Text("Órdenes", style = MaterialTheme.typography.titleLargeEmphasized) }, actions = { RadarIndicator() }, scrollBehavior = scroll)
        },
    ) { inner ->
        when (ui.content) {
            ListContent.LOADING -> Box(Modifier.fillMaxSize().padding(inner), contentAlignment = Alignment.Center) { LoadingIndicator() }
            ListContent.EMPTY -> EmptyState(OperatorIcons.Orders, "No hay órdenes", "Cuando el bot registre un pedido, aparece aquí.", Modifier.padding(inner))
            ListContent.ERROR -> EmptyState(OperatorIcons.Error, "No se pudieron cargar", "Revisa la conexión y vuelve a abrir Órdenes.", Modifier.padding(inner))
            ListContent.LIST -> LazyColumn(Modifier.fillMaxSize(), contentPadding = inner) {
                items(ui.orders, key = { it.id.raw }) { o -> OrderRow(o, onClick = { onOpen(o.id) }) }
            }
        }
    }
}

/** Ícono y tono de una etapa: atrasada en rojo, lista o entregada en verde, el resto neutro. */
@Composable
private fun stageVisual(o: OrderSummary): Triple<ImageVector, Color, Color> {
    val c = OperatorTheme.colors
    val s = MaterialTheme.colorScheme
    val icon = when (o.stage) {
        OrderStage.NEW, OrderStage.PREPARING -> OperatorIcons.Inventory
        OrderStage.READY, OrderStage.DELIVERED -> OperatorIcons.Check
        OrderStage.SHIPPING -> OperatorIcons.Shipping
        OrderStage.CANCELLED -> OperatorIcons.Close
    }
    return when {
        o.overdue -> Triple(icon, c.graveContainer, c.onGraveContainer)
        o.stage == OrderStage.READY || o.stage == OrderStage.DELIVERED -> Triple(icon, c.successContainer, c.onSuccessContainer)
        o.stage == OrderStage.CANCELLED -> Triple(icon, s.surfaceContainerHighest, s.onSurfaceVariant)
        else -> Triple(icon, s.secondaryContainer, s.onSecondaryContainer)
    }
}

@Composable
private fun OrderRow(o: OrderSummary, onClick: () -> Unit) {
    val (icon, bg, fg) = stageVisual(o)
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onClick).padding(horizontal = Spacing.margin, vertical = Spacing.md),
        horizontalArrangement = Arrangement.spacedBy(Spacing.lg),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTile(icon, bg, fg, size = 48.dp, shape = MaterialTheme.shapes.largeIncreased)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("#${o.displayId} · ${o.customer}", style = MaterialTheme.typography.titleMediumEmphasized, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm), verticalAlignment = Alignment.CenterVertically) {
                if (o.overdue) {
                    StatusPill("${stageLabel(o.stage)} · atrasada", OperatorTheme.colors.graveContainer, OperatorTheme.colors.onGraveContainer)
                } else {
                    StatusPill(stageLabel(o.stage), MaterialTheme.colorScheme.surfaceContainerHighest, MaterialTheme.colorScheme.onSurfaceVariant)
                }
                o.city?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                }
            }
        }
        Text(cop(o.totalCop), style = MaterialTheme.typography.titleSmallEmphasized)
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
    // La hoja ya aplica las barras del sistema (ModalBottomSheet consume safeDrawing).
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = Spacing.lg, end = Spacing.lg, bottom = Spacing.xl),
        verticalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        val d = ui.detail
        when {
            ui.loading -> LoadingIndicator(Modifier.align(Alignment.CenterHorizontally).padding(Spacing.xl))
            d == null -> Text(ui.message ?: "Sin datos.")
            else -> OrderSheetContent(d, ui.busy, ui.message, onAdvance = vm::advance)
        }
    }
}

@Composable
private fun OrderSheetContent(d: OrderDetail, busy: Boolean, message: String?, onAdvance: (String?, Long?) -> Unit) {
    val s = d.summary
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text("Pedido #${s.displayId}", style = MaterialTheme.typography.headlineSmallEmphasized)
        Text(s.customer, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    SectionCard("Qué compró", OperatorIcons.Inventory) {
        d.items.forEach { item ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
                Column(Modifier.weight(1f)) {
                    Text("${item.title} × ${item.quantity}", style = MaterialTheme.typography.bodyMediumEmphasized)
                    item.variant?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                Text(cop(item.totalCop), style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    SectionCard("Estado y siguiente paso", OperatorIcons.Shipping) {
        OrderStepper(s.stage)
        NextStep(s.stage, busy, onAdvance = onAdvance)
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    }
    SectionCard("Pago y envío", OperatorIcons.Payments) {
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
}

@Composable
private fun NextStep(current: OrderStage, busy: Boolean, onAdvance: (String?, Long?) -> Unit) {
    val label = advanceLabel(current) ?: return
    val next = current.next() ?: return
    var tracking by rememberSaveable { mutableStateOf("") }
    var cost by rememberSaveable { mutableStateOf("") }
    val big = Modifier.fillMaxWidth().heightIn(min = 56.dp)
    when {
        StageInput.PHOTO in next.requires ->
            // La foto de «listo» se sube con la cámara; esa pantalla todavía no está en la app.
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm), verticalAlignment = Alignment.CenterVertically) {
                Icon(OperatorIcons.Error, contentDescription = null, tint = OperatorTheme.colors.hoy, modifier = Modifier.size(18.dp))
                Text("Para marcarla lista hace falta la foto del pedido: por ahora súbela desde el dashboard.",
                    style = MaterialTheme.typography.bodySmall)
            }
        StageInput.TRACKING_URL in next.requires -> {
            OutlinedTextField(tracking, { tracking = it }, label = { Text("Link de la guía") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(cost, { cost = it }, label = { Text("Costo real del envío") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
            val ok = tracking.startsWith("http") && parseCop(cost) != null
            Button(onClick = { onAdvance(tracking.trim(), parseCop(cost)) }, shapes = ButtonDefaults.shapes(), enabled = ok && !busy, modifier = big) {
                Text(label, style = MaterialTheme.typography.titleSmall)
            }
        }
        else -> Button(onClick = { onAdvance(null, null) }, shapes = ButtonDefaults.shapes(), enabled = !busy, modifier = big) { Text(label, style = MaterialTheme.typography.titleSmall) }
    }
}

/**
 * Una sección de la ficha: tarjeta con su ícono y título. La hoja ya es `surfaceContainerLow`: la tarjeta va un tono
 * más arriba (`surfaceContainerLowest`) para que se vea.
 */
@Composable
private fun SectionCard(title: String, icon: ImageVector, content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = MaterialTheme.shapes.largeIncreased, color = MaterialTheme.colorScheme.surfaceContainerLowest, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(Spacing.lg), verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm), verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                Text(title, style = MaterialTheme.typography.titleSmallEmphasized, color = MaterialTheme.colorScheme.primary)
            }
            content()
        }
    }
}

@Composable
private fun Field(label: String, value: String) {
    Column(Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.labelSmallEmphasized, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}

@Module
@InstallIn(ActivityRetainedComponent::class)
object OrdersNavigation {
    @OptIn(ExperimentalMaterial3AdaptiveApi::class, ExperimentalMaterial3Api::class)
    @Provides @IntoSet
    fun entries(): EntryProviderInstaller = { navigator ->
        entry<OrdersKey>(
            metadata = ListDetailSceneStrategy.listPane(
                sceneKey = SceneKeys.ORDERS,
                detailPlaceholder = { EmptyState(OperatorIcons.Orders, "Elige una orden", "Su ficha se abre al tocarla.") },
            ),
        ) {
            OrdersRoute(hiltViewModel(), onOpen = { navigator.navigate(OrderSheetKey(it)) })
        }
        entry<OrderSheetKey>(metadata = BottomSheetSceneStrategy.bottomSheet(expanded = true)) { key ->
            OrderSheet(hiltViewModel<OrderSheetViewModel, OrderSheetViewModel.Factory>(creationCallback = { it.create(key.order.raw) }))
        }
    }
}
