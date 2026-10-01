package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fitInside
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.WindowInsetsRulers
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.designsystem.Avatar
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.ui.RadarFloor
import com.hubara.operator.core.ui.RadarIndicator
import java.time.ZoneId

@Composable
fun ChatScreen(
    vm: ChatViewModel,
    onBack: () -> Unit,
    onOpenOrder: (OrderId) -> Unit,
    onOpenPalette: () -> Unit,
    onReactivate: () -> Unit,
) {
    // Leído mientras está en pantalla; con la app en segundo plano, no.
    LifecycleResumeEffect(vm) {
        vm.onVisible(true)
        onPauseOrDispose { vm.onVisible(false) }
    }
    val ui by vm.state.collectAsStateWithLifecycle()
    ChatLayout(
        ui = ui, session = vm.sessionId, draft = vm.draft,
        actions = ChatActions(
            onBack = onBack, onOpenOrder = onOpenOrder, onOpenPalette = onOpenPalette, onReactivate = onReactivate,
            onReturnToBot = vm::returnToBot, onIntervene = vm::intervene, onSend = vm::send, onSendText = vm::sendText,
            onUndo = vm::undo, onRetry = vm::retry, onDismiss = vm::dismiss, onClearError = vm::clearError,
        ),
    )
}

/** Lo que el chat le pide a quien lo contiene. */
class ChatActions(
    val onBack: () -> Unit,
    val onOpenOrder: (OrderId) -> Unit,
    val onOpenPalette: () -> Unit,
    val onReactivate: () -> Unit,
    val onReturnToBot: () -> Unit,
    val onIntervene: () -> Unit,
    val onSend: (Suggestion) -> Unit,
    val onSendText: () -> Unit,
    val onUndo: (String) -> Unit,
    val onRetry: (String) -> Unit,
    val onDismiss: (String) -> Unit,
    val onClearError: () -> Unit,
)

/** El chat sin ViewModel: lo que se ve dado un [ChatUiState]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatLayout(ui: ChatUiState, session: SessionId, draft: TextFieldState, actions: ChatActions) {
    Scaffold(
        topBar = {
            ChatTopBar(
                phone = ui.phone, customerName = ui.customerName, session = session, human = ui.humanInControl,
                orderRef = ui.orderRef, onBack = actions.onBack, onOpenOrder = actions.onOpenOrder,
                onReturnToBot = actions.onReturnToBot,
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
            if (!ui.humanInControl) BotReadingPanel(ui.stage, ui.busy, actions.onIntervene)
            ui.error?.let { ErrorNotice(it, onDismiss = actions.onClearError) }
            val zone = remember { ZoneId.systemDefault() }
            val items = remember(ui.messages) { chatItems(ui.messages, System.currentTimeMillis(), zone).asReversed() }
            LazyColumn(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                reverseLayout = true,
                contentPadding = PaddingValues(start = Spacing.md, end = Spacing.md, top = Spacing.sm, bottom = Spacing.md),
            ) {
                items(
                    items,
                    key = { if (it is ChatItem.Bubble) it.message.key else "day:${(it as ChatItem.Day).label}" },
                    contentType = { if (it is ChatItem.Bubble) it.message.author else "day" },
                ) { item ->
                    when (item) {
                        is ChatItem.Day -> DayHeader(item.label)
                        is ChatItem.Bubble -> MessageBubble(item.message, position = item.position, zone = zone)
                    }
                }
            }
            // Piso del radar: los incendios que aparecen solos nunca tapan deshacer, burbujas ni lo que se escribe.
            RadarFloor {
                UndoBar(ui.pending, onUndo = actions.onUndo, onRetry = actions.onRetry, onDismiss = actions.onDismiss)
                when {
                    !ui.humanInControl -> Unit
                    !ui.windowOpen -> WindowClosedCard(actions.onReactivate)
                    else -> {
                        QuickActionStrip(ui.suggestions, onSend = actions.onSend, onEdit = { actions.onOpenPalette() }, onMore = actions.onOpenPalette)
                        Composer(draft, enabled = true, onSend = actions.onSendText)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    phone: String,
    customerName: String?,
    session: SessionId,
    human: Boolean,
    orderRef: OrderRef?,
    onBack: () -> Unit,
    onOpenOrder: (OrderId) -> Unit,
    onReturnToBot: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    TopAppBar(
        navigationIcon = { IconButton(onClick = onBack) { Icon(OperatorIcons.ArrowBack, contentDescription = "Atrás") } },
        title = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
                Avatar(customerName, session.raw, size = 40.dp)
                Column {
                    Text(chatTitle(customerName, phone), style = MaterialTheme.typography.titleMediumEmphasized, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                    Text(chatSubtitle(customerName, phone, human), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        },
        actions = {
            RadarIndicator()
            // El botón del pedido aparece solo si la conversación tiene uno asignado.
            orderRef?.let { ref ->
                val label = if (ref.count > 1) "Pedidos · ${ref.count}" else ref.displayId?.let { "Pedido #$it" } ?: "Pedido"
                AssistChip(
                    onClick = { onOpenOrder(ref.orderId) }, label = { Text(label) },
                    leadingIcon = { Icon(OperatorIcons.Orders, contentDescription = null, modifier = Modifier.size(AssistChipDefaults.IconSize)) },
                    shape = MaterialTheme.shapes.extraLarge,
                )
            }
            if (human) {
                IconButton(onClick = { menu = true }) { Icon(OperatorIcons.MoreVert, contentDescription = "Más opciones") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, shape = MaterialTheme.shapes.large) {
                    DropdownMenuItem(
                        text = { Text("Devolver al bot") }, onClick = { menu = false; onReturnToBot() },
                        leadingIcon = { Icon(OperatorIcons.Bot, contentDescription = null) },
                    )
                }
            }
        },
    )
}

/** Título del encabezado: el nombre de perfil de WhatsApp o, si no hay, el número. */
internal fun chatTitle(customerName: String?, phone: String): String = customerName ?: displayPhoneShort(phone)

/** Segunda línea: el número (cuando arriba va el nombre) y quién atiende. */
internal fun chatSubtitle(customerName: String?, phone: String, human: Boolean): String =
    listOfNotNull(customerName?.let { displayPhoneShort(phone) }, if (human) "Tú atiendes" else "El bot atiende")
        .joinToString(" · ")

private fun displayPhoneShort(raw: String): String {
    val d = raw.filter(Char::isDigit)
    return if (d.length == 12 && d.startsWith("57")) "+57 ${d.substring(2, 5)} ${d.substring(5, 8)} ${d.substring(8)}"
    else raw.ifBlank { "Cliente" }
}
