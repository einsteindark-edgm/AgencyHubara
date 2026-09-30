package com.hubara.operator.core.data.sync

import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.OrderRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.sse.EventStream
import com.hubara.operator.core.network.sse.ServerEvent
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch

/**
 * Lleva los eventos del SSE a Room. Solo corre con la app en primer plano (lo arranca `:app`); en
 * segundo plano manda el push. Los chats abiertos se «vigilan» para refrescar su historial y burbujas.
 */
@Singleton
class SyncEngine @Inject constructor(
    private val events: EventStream,
    private val conversations: ConversationRepository,
    private val chats: ChatRepository,
    private val suggestions: SuggestionRepository,
    private val fires: FireRepository,
    private val orders: OrderRepository,
) {
    private val watched = ConcurrentHashMap<SessionId, AtomicInteger>()
    private val fireRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /** Marca un chat como abierto mientras la pantalla lo muestre. Cerrar el handle deja de vigilarlo. */
    fun watch(sessionId: SessionId): AutoCloseable {
        watched.getOrPut(sessionId) { AtomicInteger(0) }.incrementAndGet()
        return AutoCloseable { watched[sessionId]?.let { if (it.decrementAndGet() <= 0) watched.remove(sessionId) } }
    }

    fun isWatched(sessionId: SessionId): Boolean = (watched[sessionId]?.get() ?: 0) > 0

    /** Corre hasta que se cancele: carga inicial y después los eventos. */
    suspend fun run() = coroutineScope {
        launch { refreshOnSignal(fireRequests, FIRE_MIN_GAP_MS) { fires.refresh() } }
        conversations.refresh()
        fires.refresh()
        orders.refreshList()
        events.events().collect { handle(it) }
    }

    suspend fun handle(event: ServerEvent) {
        when (event) {
            is ServerEvent.SessionsSnapshot -> {
                conversations.applySnapshot(event.conversations)
                fireRequests.tryEmit(Unit)
            }
            is ServerEvent.SessionUpdated -> {
                if (isWatched(event.sessionId)) {
                    chats.refresh(event.sessionId)
                    suggestions.refresh(event.sessionId)
                }
                fireRequests.tryEmit(Unit)
            }
            ServerEvent.OrdersChanged -> {
                fireRequests.tryEmit(Unit)
                orders.refreshList()
            }
            is ServerEvent.Unknown -> Unit
        }
    }

    companion object {
        const val FIRE_MIN_GAP_MS = 1_500L
    }
}

/**
 * Recarga apenas llega una señal, sin esperar (un incendio grave se ve ya). Las señales que llegan
 * mientras tanto se juntan en una sola recarga, como mucho una cada [minGapMs].
 */
internal suspend fun refreshOnSignal(signals: Flow<Unit>, minGapMs: Long, refresh: suspend () -> Unit) {
    signals.conflate().collect {
        refresh()
        delay(minGapMs)
    }
}
