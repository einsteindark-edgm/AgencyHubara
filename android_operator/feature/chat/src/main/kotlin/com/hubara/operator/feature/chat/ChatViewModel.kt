package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.foundation.text.input.clearText
import androidx.compose.runtime.snapshotFlow
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.PendingAction
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.data.sync.SyncEngine
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.network.di.ApiConfig
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class ChatUiState(
    val phone: String = "",
    val humanInControl: Boolean = false,
    val orderRef: OrderRef? = null,
    val windowOpen: Boolean = true,
    val stage: String? = null,
    val messages: ImmutableList<Message> = persistentListOf(),
    val suggestions: ImmutableList<Suggestion> = persistentListOf(),
    val pending: ImmutableList<PendingAction> = persistentListOf(),
    val busy: Boolean = false,
    val error: String? = null,
)

@OptIn(FlowPreview::class)
@HiltViewModel(assistedFactory = ChatViewModel.Factory::class)
class ChatViewModel @AssistedInject constructor(
    @Assisted sessionRaw: String,
    private val chats: ChatRepository,
    private val suggestions: SuggestionRepository,
    private val outbox: OutboxRepository,
    private val sync: SyncEngine,
    private val clock: Clock,
    private val config: ApiConfig,
    private val seen: SeenRepository,
) : ViewModel() {
    val sessionId: SessionId = requireNotNull(SessionId.parse(sessionRaw)) { "sesión inválida" }

    /** El borrador del composer. Vive en Room: ni reiniciar la app lo pierde. */
    val draft = TextFieldState()

    private val busy = MutableStateFlow(false)
    private val error = MutableStateFlow<String?>(null)
    private val watch = sync.watch(sessionId)

    val state: StateFlow<ChatUiState> = combine(
        chats.observeChat(sessionId), suggestions.observe(sessionId), busy, error,
    ) { chat, set, b, e ->
        val human = chat.route == Route.HUMAN
        ChatUiState(
            phone = chat.phone,
            humanInControl = human,
            orderRef = chat.orderRef,
            windowOpen = chat.windowExpiresAtMs?.let { it > clock.nowMs() } ?: true,
            stage = set?.stage,
            // Las fotos se cargan con el token de sesión, y solo desde nuestro backend.
            messages = chat.messages.map { m -> m.copy(imageUrl = config.mediaUrl(m.imageUrl)) }.toImmutableList(),
            // Las burbujas solo tienen sentido con el humano en control y la ventana abierta.
            suggestions = if (human && set?.windowOpen != false) set?.suggestions.orEmpty().toImmutableList() else persistentListOf(),
            pending = chat.pendingActions.toImmutableList(),
            busy = b,
            error = e,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), ChatUiState())

    init {
        viewModelScope.launch {
            val saved = chats.loadDraft(sessionId)
            if (saved.isNotEmpty() && draft.text.isEmpty()) draft.edit { replace(0, length, saved) }
            snapshotFlow { draft.text.toString() }.drop(1).distinctUntilChanged().debounce(400)
                .collect { chats.saveDraft(sessionId, it, clock.nowMs()) }
        }
        refresh()
    }

    private var seenJob: Job? = null

    /** Con el chat a la vista, lo que escribe el cliente queda leído (el contador de la bandeja no sube). */
    fun onVisible(visible: Boolean) {
        seenJob?.cancel()
        seenJob = if (visible) viewModelScope.launch { seen.keepSeen(sessionId) } else null
    }

    fun refresh() {
        viewModelScope.launch {
            val failed = chats.refresh(sessionId).isFailure
            suggestions.refresh(sessionId)
            if (failed) error.value = "Sin conexión: mostrando lo último guardado."
        }
    }

    fun intervene() = act("No se pudo tomar la conversación.") {
        chats.intervene(sessionId).also { if (it.isSuccess) suggestions.refresh(sessionId) }
    }

    fun returnToBot() = act("No se pudo devolver la conversación al bot.") { chats.returnToBot(sessionId) }

    fun sendText() {
        val text = draft.text.toString().trim()
        if (text.isEmpty()) return
        viewModelScope.launch {
            outbox.sendText(sessionId, text)
            draft.clearText()
            chats.saveDraft(sessionId, "", clock.nowMs())
        }
    }

    fun send(suggestion: Suggestion) {
        viewModelScope.launch { outbox.sendTool(sessionId, suggestion.action, suggestion.label) }
    }

    fun undo(id: String) {
        viewModelScope.launch { if (!outbox.undo(id)) error.value = "Ya se estaba enviando: no se pudo deshacer." }
    }

    fun retry(id: String) { viewModelScope.launch { outbox.retry(id) } }

    fun dismiss(id: String) { viewModelScope.launch { outbox.dismiss(id) } }

    fun clearError() { error.value = null }

    private fun act(failure: String, block: suspend () -> Result<Unit>) {
        if (busy.value) return
        viewModelScope.launch {
            busy.value = true
            if (block().isFailure) error.value = failure
            busy.value = false
        }
    }

    override fun onCleared() = watch.close()

    @AssistedFactory
    interface Factory {
        fun create(sessionRaw: String): ChatViewModel
    }
}
