package com.hubara.operator.feature.chat

import com.hubara.operator.core.ui.RadarIndicator
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fitInside
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.WindowInsetsRulers
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    vm: ChatViewModel,
    onBack: () -> Unit,
    onOpenOrder: (OrderId) -> Unit,
    onOpenPalette: () -> Unit,
    onReactivate: () -> Unit,
) {
    val ui by vm.state.collectAsStateWithLifecycle()
    Scaffold(
        topBar = {
            ChatTopBar(
                phone = ui.phone, human = ui.humanInControl, orderRef = ui.orderRef,
                onBack = onBack, onOpenOrder = onOpenOrder, onReturnToBot = vm::returnToBot,
            )
        },
    ) { inner ->
        // Skill edge-to-edge: fitInside(Ime) sube el composer con el teclado sin doble padding.
        Column(
            Modifier
                .padding(inner)
                .consumeWindowInsets(inner)
                .fitInside(WindowInsetsRulers.Ime.current),
        ) {
            if (!ui.humanInControl) BotReadingPanel(ui.stage, ui.busy, vm::intervene)
            ui.error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
            }
            val reversed = remember(ui.messages) { ui.messages.asReversed() }
            LazyColumn(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                reverseLayout = true,
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                items(reversed, key = { it.key }, contentType = { it.author }) { MessageBubble(it) }
            }
            UndoBar(ui.pending, onUndo = vm::undo, onRetry = vm::retry, onDismiss = vm::dismiss)
            when {
                !ui.humanInControl -> Unit
                !ui.windowOpen -> Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("La ventana de 24 h está cerrada: para escribirle hay que reactivar la conversación con una plantilla.",
                        style = MaterialTheme.typography.bodySmall)
                    Button(onClick = onReactivate, modifier = Modifier.fillMaxWidth()) { Text("Reactivar con plantilla") }
                }
                else -> {
                    QuickActionStrip(ui.suggestions, onSend = vm::send, onEdit = { onOpenPalette() }, onMore = onOpenPalette)
                    Composer(vm.draft, enabled = true, onSend = vm::sendText)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    phone: String,
    human: Boolean,
    orderRef: OrderRef?,
    onBack: () -> Unit,
    onOpenOrder: (OrderId) -> Unit,
    onReturnToBot: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    TopAppBar(
        navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Atrás") } },
        title = {
            Column {
                Text(displayPhoneShort(phone), style = MaterialTheme.typography.titleMedium)
                Text(if (human) "Tú atiendes" else "El bot atiende", style = MaterialTheme.typography.labelSmall)
            }
        },
        actions = {
            RadarIndicator()
            // El botón del pedido aparece solo si la conversación tiene uno asignado.
            orderRef?.let { ref ->
                val label = if (ref.count > 1) "Pedidos · ${ref.count}" else ref.displayId?.let { "Pedido #$it" } ?: "Pedido"
                AssistChip(onClick = { onOpenOrder(ref.orderId) }, label = { Text(label) })
            }
            if (human) {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = "Más opciones") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("Devolver al bot") }, onClick = { menu = false; onReturnToBot() })
                }
            }
        },
    )
}

private fun displayPhoneShort(raw: String): String {
    val d = raw.filter(Char::isDigit)
    return if (d.length == 12 && d.startsWith("57")) "+57 ${d.substring(2, 5)} ${d.substring(5, 8)} ${d.substring(8)}"
    else raw.ifBlank { "Cliente" }
}
