package com.hubara.operator.feature.inbox

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.designsystem.Avatar
import com.hubara.operator.core.designsystem.EmptyState
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.ui.NotificationPermissionBanner
import com.hubara.operator.core.ui.RadarIndicator
import com.hubara.operator.core.ui.listTimeLabel
import java.time.ZoneId
import kotlinx.collections.immutable.persistentListOf
import com.hubara.operator.core.ui.LocalSessionActions
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.material3.TextButton
import androidx.compose.material3.IconButton
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.AlertDialog

@Composable
fun InboxRoute(vm: InboxViewModel, onOpen: (Conversation) -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    val session = LocalSessionActions.current
    InboxScreen(ui, onFilter = vm::setFilter, onOpen = onOpen, onSignOut = session?.signOut, onOpenPrivacy = session?.openPrivacy)
}

/** La bandeja: filtros, aviso de notificaciones y una fila por conversación (avatar, nombre, hora, último mensaje). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InboxScreen(
    ui: InboxUiState,
    onFilter: (InboxFilter) -> Unit,
    onOpen: (Conversation) -> Unit,
    onSignOut: (() -> Unit)? = null,
    onOpenPrivacy: (() -> Unit)? = null,
    nowMs: Long = remember(ui) { System.currentTimeMillis() },
    zone: ZoneId = remember { ZoneId.systemDefault() },
) {
    val scroll = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            TopAppBar(
                title = { Text("Chats", style = MaterialTheme.typography.titleLargeEmphasized) },
                actions = {
                    RadarIndicator()
                    if (onSignOut != null || onOpenPrivacy != null) SessionMenu(onSignOut, onOpenPrivacy)
                },
                scrollBehavior = scroll,
            )
        },
    ) { inner ->
        val start = inner.calculateLeftPadding(LayoutDirection.Ltr)
        val end = inner.calculateRightPadding(LayoutDirection.Ltr)
        Column(Modifier.fillMaxSize().padding(top = inner.calculateTopPadding(), start = start, end = end)) {
            LazyRow(
                contentPadding = PaddingValues(horizontal = Spacing.margin),
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                items(InboxFilter.entries) { f ->
                    val selected = ui.filter == f
                    FilterChip(
                        selected = selected, onClick = { onFilter(f) }, label = { Text(f.label) },
                        leadingIcon = if (selected) {
                            { Icon(OperatorIcons.Check, contentDescription = null, modifier = Modifier.size(FilterChipDefaults.IconSize)) }
                        } else null,
                        shape = CircleShape,
                    )
                }
            }
            NotificationPermissionBanner()
            if (ui.offline) OfflineNotice()
            if (ui.rows.isEmpty()) {
                EmptyState(OperatorIcons.Chat, "No hay chats aquí", emptyHint(ui.filter))
            }
            // El padding inferior del Scaffold va en contentPadding: la lista se desliza detrás de la barra.
            LazyColumn(contentPadding = PaddingValues(top = Spacing.xs, bottom = inner.calculateBottomPadding() + Spacing.sm)) {
                items(ui.rows, key = { it.conversation.sessionId.raw }) { row ->
                    ConversationRow(row.conversation, row.unseen, nowMs, zone, onClick = { onOpen(row.conversation) })
                }
            }
        }
    }
}

/**
 * «Más opciones» de la bandeja: la política de privacidad y cerrar sesión (con confirmación, porque borra lo guardado
 * en el teléfono).
 */
@Composable
private fun SessionMenu(onSignOut: (() -> Unit)?, onOpenPrivacy: (() -> Unit)?) {
    var menu by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    IconButton(onClick = { menu = true }) { Icon(OperatorIcons.MoreVert, contentDescription = "Más opciones") }
    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, shape = MaterialTheme.shapes.large) {
        onOpenPrivacy?.let { open -> DropdownMenuItem(text = { Text("Política de privacidad") }, onClick = { menu = false; open() }) }
        if (onSignOut != null) DropdownMenuItem(text = { Text("Cerrar sesión") }, onClick = { menu = false; confirm = true })
    }
    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("¿Cerrar sesión?") },
            text = {
                Text("Se borran de este teléfono los chats, borradores y avisos guardados. Para volver a entrar necesitas tu email y contraseña.")
            },
            confirmButton = { TextButton(onClick = { confirm = false; onSignOut?.invoke() }) { Text("Cerrar sesión") } },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancelar") } },
        )
    }
}

private fun emptyHint(filter: InboxFilter): String = when (filter) {
    InboxFilter.TODOS -> "Cuando un cliente escriba, su conversación aparece aquí."
    InboxFilter.NO_LEIDOS -> "Estás al día: ya leíste todo."
    InboxFilter.HUMANO -> "Ninguna conversación está en tus manos ahora."
    InboxFilter.CON_PEDIDO -> "Ninguna conversación tiene pedido todavía."
}

@Composable
private fun OfflineNotice() {
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        modifier = Modifier.padding(horizontal = Spacing.margin, vertical = Spacing.xs),
    ) {
        Row(Modifier.padding(horizontal = Spacing.md, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(OperatorIcons.Error, contentDescription = null, modifier = Modifier.size(16.dp))
            Text("Sin conexión: mostrando lo último guardado.", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(start = 6.dp))
        }
    }
}

@Composable
private fun ConversationRow(c: Conversation, unseen: Int, nowMs: Long, zone: ZoneId, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val unread = unseen > 0
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = Spacing.margin, vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.lg),
    ) {
        Avatar(c.customerName, c.sessionId.raw, size = 52.dp)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    conversationTitle(c), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                    style = if (unread) MaterialTheme.typography.titleMediumEmphasized else MaterialTheme.typography.titleMedium,
                )
                Text(
                    listTimeLabel(c.lastUpdatedMs, nowMs, zone),
                    style = if (unread) MaterialTheme.typography.labelMediumEmphasized else MaterialTheme.typography.labelMedium,
                    color = if (unread) colors.primary else colors.onSurfaceVariant,
                    modifier = Modifier.padding(start = Spacing.sm),
                )
            }
            inboxPreview(c.lastMessagePreview)?.let { preview ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        preview, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                        style = if (unread) MaterialTheme.typography.bodyMediumEmphasized else MaterialTheme.typography.bodyMedium,
                        color = if (unread) colors.onSurface else colors.onSurfaceVariant,
                    )
                    if (unread) UnreadBadge(unseen)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    if (c.route == Route.HUMAN) OperatorIcons.Person else OperatorIcons.Bot, contentDescription = null,
                    tint = if (c.route == Route.HUMAN) colors.primary else colors.tertiary, modifier = Modifier.size(14.dp),
                )
                Text(
                    conversationDetail(c), style = MaterialTheme.typography.labelMedium, color = colors.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).padding(start = 4.dp),
                )
                if (unread && c.lastMessagePreview == null) UnreadBadge(unseen)
            }
        }
    }
}

@Composable
private fun UnreadBadge(unseen: Int) {
    Surface(
        shape = CircleShape, color = MaterialTheme.colorScheme.primary, contentColor = MaterialTheme.colorScheme.onPrimary,
        modifier = Modifier.padding(start = Spacing.sm)
            .semantics { contentDescription = if (unseen == 1) "1 mensaje sin leer" else "$unseen mensajes sin leer" },
    ) {
        Text(
            unseen.toString(), style = MaterialTheme.typography.labelMediumEmphasized,
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 1.dp),
        )
    }
}

@Preview(showBackground = true, heightDp = 640)
@Composable
private fun InboxPreview() = OperatorTheme {
    val now = 1_790_000_000_000
    fun conv(n: Int, name: String?, route: Route, tag: String, preview: String?, minutesAgo: Int) = Conversation(
        sessionId = SessionId.parse("wa_00000000010$n")!!, phone = "00000000010$n", tag = tag, route = route,
        lastUpdatedMs = now - minutesAgo * 60_000L, lastInboundMs = null, inboundCount = 3, orderRef = null,
        customerName = name, lastMessagePreview = preview,
    )
    InboxScreen(
        InboxUiState(
            rows = persistentListOf(
                InboxRow(conv(1, "Laura Prueba", Route.BOT, "INTERESADO", "¿y qué aromas tienen? quiero 2", 3), 2),
                InboxRow(conv(2, "Sofía Prueba", Route.HUMAN, "HUMANO", "Me confirmas el precio del duo porfa", 15), 0),
                InboxRow(conv(3, null, Route.BOT, "INTERESADO", null, 60 * 26), 0),
            ),
        ),
        onFilter = {}, onOpen = {}, nowMs = now,
    )
}
