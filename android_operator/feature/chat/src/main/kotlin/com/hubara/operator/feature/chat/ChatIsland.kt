package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Column
import com.hubara.operator.core.designsystem.LoadingState
import com.hubara.operator.core.designsystem.LoadError
import androidx.compose.ui.Alignment
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.ui.NativeComponent
import com.hubara.operator.core.ui.NativeProps
import com.hubara.operator.core.ui.RadarFloor
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dagger.multibindings.IntoMap
import dagger.multibindings.StringKey
import java.time.ZoneId

/**
 * El chat como pieza nativa de las pantallas del servidor (`{"type": "chat", "session": …}`): lo que entendió el bot,
 * el historial, deshacer, las burbujas y el composer. El encabezado (nombre, «Pedido #41», «Devolver al bot») lo arma el
 * JSON de la pantalla `chat`; «+ Más» y «Reactivar con plantilla» hacen lo que diga el JSON (`on_more`, `on_reactivate`).
 */
object ChatNativeComponent : NativeComponent {
    @Composable
    override fun Content(props: NativeProps, modifier: Modifier) {
        val session = props["session"]?.let(SessionId::parse) ?: return
        val vm = hiltViewModel<ChatViewModel, ChatViewModel.Factory>(key = "chat:${session.raw}", creationCallback = { it.create(session.raw) })
        ChatIsland(vm, onMore = props.action("on_more") ?: {}, onReactivate = props.action("on_reactivate") ?: {}, modifier = modifier)
    }
}

@Module
@InstallIn(SingletonComponent::class)
object ChatNativeModule {
    @Provides @IntoMap @StringKey("chat")
    fun chat(): NativeComponent = ChatNativeComponent
}

@Composable
fun ChatIsland(vm: ChatViewModel, onMore: () -> Unit, onReactivate: () -> Unit, modifier: Modifier = Modifier) {
    // Leído mientras está en pantalla; con la app en segundo plano, no.
    LifecycleResumeEffect(vm) {
        vm.onVisible(true)
        onPauseOrDispose { vm.onVisible(false) }
    }
    val ui by vm.state.collectAsStateWithLifecycle()
    ChatBody(
        ui = ui, draft = vm.draft, modifier = modifier,
        actions = ChatActions(
            onMore = onMore, onReactivate = onReactivate, onIntervene = vm::intervene, onSend = vm::send, onSendText = vm::sendText,
            onUndo = vm::undo, onRetry = vm::retry, onDismiss = vm::dismiss, onClearError = vm::clearError, onReload = vm::refresh,
        ),
    )
}

/** Lo que el chat le pide a quien lo contiene. */
class ChatActions(
    val onMore: () -> Unit,
    val onReactivate: () -> Unit,
    val onIntervene: () -> Unit,
    val onSend: (Suggestion) -> Unit,
    val onSendText: () -> Unit,
    val onUndo: (String) -> Unit,
    val onRetry: (String) -> Unit,
    val onDismiss: (String) -> Unit,
    val onClearError: () -> Unit,
    val onReload: () -> Unit = {},
)

/** El chat sin ViewModel: lo que se ve dado un [ChatUiState]. */
@Composable
fun ChatBody(ui: ChatUiState, draft: TextFieldState, actions: ChatActions, modifier: Modifier = Modifier) {
    Column(modifier) {
        if (!ui.humanInControl) BotReadingPanel(ui.stage, ui.busy, actions.onIntervene)
        ui.error?.let { ErrorNotice(it, onDismiss = actions.onClearError) }
        when (ui.history) {
            HistoryView.LOADING -> LoadingState(Modifier.weight(1f))
            HistoryView.FAILED -> Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                LoadError(actions.onReload, title = "No se pudo cargar la conversación")
            }
            HistoryView.MESSAGES -> History(ui, Modifier.weight(1f))
        }
        // Piso del radar: los incendios que aparecen solos nunca tapan deshacer, burbujas ni lo que se escribe.
        RadarFloor {
            UndoBar(ui.pending, onUndo = actions.onUndo, onRetry = actions.onRetry, onDismiss = actions.onDismiss)
            when {
                !ui.humanInControl -> Unit
                !ui.windowOpen -> WindowClosedCard(actions.onReactivate)
                else -> {
                    QuickActionStrip(ui.suggestions, onSend = actions.onSend, onEdit = { actions.onMore() }, onMore = actions.onMore)
                    Composer(draft, enabled = true, onSend = actions.onSendText)
                }
            }
        }
    }
}

/** El historial, del más nuevo abajo. */
@Composable
private fun History(ui: ChatUiState, modifier: Modifier) {
    val zone = remember { ZoneId.systemDefault() }
    val items = remember(ui.messages) { chatItems(ui.messages, System.currentTimeMillis(), zone).asReversed() }
    LazyColumn(
        modifier = modifier.fillMaxWidth(),
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
}
