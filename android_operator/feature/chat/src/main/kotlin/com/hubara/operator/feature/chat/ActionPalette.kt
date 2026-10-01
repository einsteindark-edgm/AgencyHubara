package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.SegmentGap
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.segmentedShape
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.SessionId
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.launch

/** Acciones que no necesitan elegir un producto. Las que sí, esperan al selector del catálogo. */
internal val PALETTE: List<Pair<String, List<Pair<String, String>>>> = listOf(
    "Catálogo" to listOf("present_products" to "Enviar productos"),
    "Venta" to listOf("request_shipping_details" to "Pedir datos de envío", "send_shipping_rates" to "Tarifas de envío"),
    "Cierre" to listOf("present_order_confirmation" to "Resumen para confirmar", "send_payment_methods" to "Medios de pago"),
)

@HiltViewModel(assistedFactory = ActionPaletteViewModel.Factory::class)
class ActionPaletteViewModel @AssistedInject constructor(
    @Assisted sessionRaw: String,
    private val outbox: OutboxRepository,
) : ViewModel() {
    private val sessionId = requireNotNull(SessionId.parse(sessionRaw))

    fun send(tool: String, label: String) {
        viewModelScope.launch { outbox.sendTool(sessionId, ActionRef(tool), label) }
    }

    @AssistedFactory
    interface Factory { fun create(sessionRaw: String): ActionPaletteViewModel }
}

@Composable
private fun actionIcon(tool: String): ImageVector = when (tool) {
    "present_products" -> OperatorIcons.Inventory
    "request_shipping_details", "send_shipping_rates" -> OperatorIcons.Shipping
    "send_payment_methods" -> OperatorIcons.Payments
    else -> OperatorIcons.Check
}

/** Todas las acciones del bot que el operador puede disparar, agrupadas en listas segmentadas. */
@Composable
fun ActionPalette(vm: ActionPaletteViewModel, onDone: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(start = Spacing.lg, end = Spacing.lg, bottom = Spacing.xl)) {
        Text("Acciones", style = MaterialTheme.typography.titleLargeEmphasized, modifier = Modifier.padding(bottom = Spacing.sm))
        PALETTE.forEach { (group, actions) ->
            Text(group, style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = Spacing.xs, top = Spacing.md, bottom = Spacing.sm))
            Column(verticalArrangement = Arrangement.spacedBy(SegmentGap)) {
                actions.forEachIndexed { i, (tool, label) ->
                    Surface(
                        onClick = { vm.send(tool, label); onDone() },
                        shape = segmentedShape(i, actions.size),
                        color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    ) {
                        ListItem(
                            headlineContent = { Text(label, style = MaterialTheme.typography.bodyLarge) },
                            leadingContent = {
                                IconTile(actionIcon(tool), MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.colorScheme.onSecondaryContainer)
                            },
                            colors = ListItemDefaults.colors(containerColor = androidx.compose.ui.graphics.Color.Transparent),
                        )
                    }
                }
            }
        }
    }
}
