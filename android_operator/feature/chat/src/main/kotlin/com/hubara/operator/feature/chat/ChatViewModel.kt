package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.foundation.text.input.clearText
import androidx.compose.runtime.snapshotFlow
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hubara.operator.core.data.ApplicationScope
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.PENDING_KEY_PREFIX
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.async
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ChatUiState(
    val phone: String = "",
    val customerName: String? = null,
    val humanInControl: Boolean = false,
    val orderRef: OrderRef? = null,
    val windowOpen: Boolean = true,
    val stage: String? = null,
    val messages: ImmutableList<Message> = persistentListOf(),
    val suggestions: ImmutableList<Suggestion> = persistentListOf(),
    val pending: ImmutableList<PendingAction> = persistentListOf(),
    val busy: Boolean = false,
    val error: String? = null,
    /** Qué va donde va el historial: los mensajes, «Cargando…» o el error con reintentar. */
    val history: HistoryView = HistoryView.MESSAGES,
    /** El operador tiró hacia abajo y se está pidiendo la conversación al servidor. */
    val refreshing: Boolean = false,
    /** Las fotos elegidas que salen al tocar «Enviar» (`content://` del selector), en el orden en que se eligieron. */
    val attachments: ImmutableList<String> = persistentListOf(),
)

/**
 * Lo que se pinta de un mensaje: las fotos del historial solo desde nuestro backend (con el token de la sesión) y las
 * de una tarjeta también del CDN de los productos (`https://`, sin token). La foto que todavía está saliendo es un
 * archivo del teléfono: solo la burbuja del outbox puede mostrarlo.
 */
internal fun Message.forDisplay(mediaUrl: (String?) -> String?): Message {
    val local = key.startsWith(PENDING_KEY_PREFIX) && imageUrl?.startsWith("file://") == true
    return copy(imageUrl = if (local) imageUrl else mediaUrl(imageUrl), card = card?.reachable(mediaUrl))
}

/** Qué hace «Enviar» con lo que hay en el composer. */
internal sealed interface SendPlan {
    data object Nothing : SendPlan
    data class Text(val text: String) : SendPlan
    /** Cada foto en su mensaje; el texto, como pie de la primera. */
    data class Photos(val sources: List<String>, val caption: String?) : SendPlan
    /** WhatsApp corta el pie de una foto en 1024 caracteres: se avisa antes de subir nada. */
    data object CaptionTooLong : SendPlan
}

internal fun sendPlan(text: String, attachments: List<String>): SendPlan {
    val trimmed = text.trim()
    return when {
        attachments.isEmpty() -> if (trimmed.isEmpty()) SendPlan.Nothing else SendPlan.Text(trimmed)
        trimmed.length > OutboxRepository.MAX_CAPTION -> SendPlan.CaptionTooLong
        else -> SendPlan.Photos(attachments, trimmed.ifEmpty { null })
    }
}

/** Cómo va la carga del historial desde el backend. */
enum class HistoryLoad { LOADING, LOADED, FAILED }

enum class HistoryView { LOADING, FAILED, MESSAGES }

/**
 * Con mensajes guardados en Room se ven al instante (la recarga va callada); sin nada guardado, «Cargando…» mientras
 * llega el historial, y si no llega, el error con reintentar: nunca un chat vacío que parece no tener mensajes.
 */
fun historyLoad(load: HistoryLoad, hasMessages: Boolean): HistoryView = when {
    hasMessages -> HistoryView.MESSAGES
    load == HistoryLoad.LOADING -> HistoryView.LOADING
    load == HistoryLoad.FAILED -> HistoryView.FAILED
    else -> HistoryView.MESSAGES
}

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
    @ApplicationScope private val appScope: CoroutineScope,
) : ViewModel() {
    val sessionId: SessionId = requireNotNull(SessionId.parse(sessionRaw)) { "sesión inválida" }

    /** El borrador del composer. Vive en Room: ni reiniciar la app lo pierde. */
    val draft = TextFieldState()

    private val busy = MutableStateFlow(false)
    private val error = MutableStateFlow<String?>(null)
    private val load = MutableStateFlow(HistoryLoad.LOADING)
    private val pulling = MutableStateFlow(false)
    private val watch = sync.watch(sessionId)

    private val attachments = MutableStateFlow<ImmutableList<String>>(persistentListOf())

    val state: StateFlow<ChatUiState> = combine(
        chats.observeChat(sessionId), suggestions.observe(sessionId), combine(busy, pulling, attachments, ::Triple), error, load,
    ) { chat, set, (b, pulled, picked), e, l ->
        val human = chat.route == Route.HUMAN
        ChatUiState(
            phone = chat.phone,
            customerName = chat.customerName,
            humanInControl = human,
            orderRef = chat.orderRef,
            windowOpen = chat.windowExpiresAtMs?.let { it > clock.nowMs() } ?: true,
            stage = set?.stage,
            messages = chat.messages.map { it.forDisplay(config::mediaUrl) }.toImmutableList(),
            // Las burbujas solo tienen sentido con el humano en control y la ventana abierta.
            suggestions = if (human && set?.windowOpen != false) set?.suggestions.orEmpty().toImmutableList() else persistentListOf(),
            pending = chat.pendingActions.toImmutableList(),
            busy = b,
            error = e,
            history = historyLoad(l, hasMessages = chat.messages.isNotEmpty()),
            refreshing = pulled,
            attachments = picked,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), ChatUiState(history = HistoryView.LOADING))

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
            load.value = HistoryLoad.LOADING
            val failed = chats.refresh(sessionId).isFailure
            load.value = if (failed) HistoryLoad.FAILED else HistoryLoad.LOADED
            // Sin nada guardado, el error con reintentar ya lo dice; con mensajes, el aviso de que son los guardados.
            if (failed && chats.observeChat(sessionId).first().messages.isNotEmpty()) error.value = OFFLINE
            suggestions.refresh(sessionId)
        }
    }

    /** Tirar hacia abajo: la conversación al servidor ya y, si el tiempo real esperaba para reconectar, que reconecte. */
    fun pull() {
        if (pulling.value) return
        viewModelScope.launch {
            pulling.value = true
            sync.reconnectNow()
            val failed = chats.refresh(sessionId).isFailure
            if (failed) {
                error.value = OFFLINE
            } else {
                load.value = HistoryLoad.LOADED
                if (error.value == OFFLINE) error.value = null
            }
            suggestions.refresh(sessionId)
            pulling.value = false
        }
    }

    fun intervene() = act("No se pudo tomar la conversación.") {
        chats.intervene(sessionId).also { if (it.isSuccess) suggestions.refresh(sessionId) }
    }

    fun returnToBot() = act("No se pudo devolver la conversación al bot.") { chats.returnToBot(sessionId) }

    /** Las fotos que devolvió el selector se suman a las ya elegidas (sin repetir y hasta el máximo). */
    fun attach(sources: List<String>) {
        attachments.update { (it + sources).distinct().take(OutboxRepository.MAX_PHOTOS).toImmutableList() }
    }

    fun removeAttachment(source: String) {
        attachments.update { (it - source).toImmutableList() }
    }

    fun sendText() {
        when (val plan = sendPlan(draft.text.toString(), attachments.value)) {
            SendPlan.Nothing -> Unit
            SendPlan.CaptionTooLong -> error.value = "El texto de una foto puede tener hasta ${OutboxRepository.MAX_CAPTION} caracteres."
            is SendPlan.Text -> viewModelScope.launch {
                outbox.sendText(sessionId, plan.text)
                clearDraft()
            }
            is SendPlan.Photos -> {
                // Fuera del composer ya: un segundo toque mientras se preparan no las manda dos veces.
                attachments.value = persistentListOf()
                val typed = draft.text.toString()
                viewModelScope.launch {
                    clearDraft()
                    // Prepararlas tarda (decodificar y comprimir): va en el alcance de la app para que salgan todas
                    // aunque el operador salga del chat apenas toca «Enviar».
                    val batch = appScope.async { outbox.sendPhotos(sessionId, plan.sources, plan.caption) }.await()
                    if (batch.unreadable > 0) error.value = unreadablePhotos(batch.unreadable)
                    // Ninguna se pudo leer: el texto no salió con ninguna y vuelve al campo.
                    if (batch.sent.isEmpty() && draft.text.isEmpty()) draft.edit { replace(0, length, typed) }
                }
            }
        }
    }

    private suspend fun clearDraft() {
        draft.clearText()
        chats.saveDraft(sessionId, "", clock.nowMs())
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

private fun unreadablePhotos(n: Int) =
    if (n == 1) "No se pudo leer una de las fotos: no se envió." else "No se pudieron leer $n fotos: no se enviaron."

private const val OFFLINE = "Sin conexión: mostrando lo último guardado. Desliza hacia abajo para reintentar."
