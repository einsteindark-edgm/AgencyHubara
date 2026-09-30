package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.foundation.clickable
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.outbox.OutboxRepository
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
fun ActionPalette(vm: ActionPaletteViewModel, onDone: () -> Unit) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 16.dp)) {
        PALETTE.forEach { (group, actions) ->
            Text(group, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            actions.forEach { (tool, label) ->
                ListItem(
                    headlineContent = { Text(label) },
                    modifier = Modifier.clickable { vm.send(tool, label); onDone() },
                )
            }
        }
    }
}
