package com.hubara.operator.feature.inbox

import com.hubara.operator.core.ui.NotificationPermissionBanner
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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Badge
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.model.Conversation

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InboxScreen(vm: InboxViewModel, onOpen: (Conversation) -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    Scaffold(topBar = { TopAppBar(title = { Text("Chats") }, actions = { RadarIndicator() }) }) { inner ->
        Column(Modifier.fillMaxSize().padding(top = inner.calculateTopPadding())) {
            LazyRow(contentPadding = PaddingValues(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(InboxFilter.entries) { f ->
                    FilterChip(selected = ui.filter == f, onClick = { vm.setFilter(f) }, label = { Text(f.label) })
                }
            }
            NotificationPermissionBanner()
            if (ui.offline) {
                Text("Sin conexión: mostrando lo último guardado.", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
            }
            // El padding inferior del Scaffold va en contentPadding: la lista se desliza detrás de la barra.
            LazyColumn(contentPadding = PaddingValues(bottom = inner.calculateBottomPadding())) {
                items(ui.rows, key = { it.conversation.sessionId.raw }) { row ->
                    ConversationRow(row.conversation, row.unseen, onClick = { onOpen(row.conversation) })
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun ConversationRow(c: Conversation, unseen: Int, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(displayPhone(c.phone), style = MaterialTheme.typography.titleSmall)
            Text(conversationSubtitle(c.route, c.tag, c.orderRef), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (unseen > 0) {
            Badge(Modifier.semantics { contentDescription = if (unseen == 1) "1 mensaje sin leer" else "$unseen mensajes sin leer" }) {
                Text(unseen.toString())
            }
        }
    }
}
