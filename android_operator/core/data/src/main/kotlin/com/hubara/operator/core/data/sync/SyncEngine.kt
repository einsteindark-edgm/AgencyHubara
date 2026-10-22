package com.hubara.operator.core.data.sync

import android.util.Log
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.OrderRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.data.screens.ServerChanges
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.sse.EventStream
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.sse.StreamUpdate
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Lo que las pantallas necesitan de la sincronización: su estado, la bandeja al día sin pedirla y «sincronizar ya». */
interface LiveSync {
    /** Si está en vivo, qué llegó y cuánto tardó (la pantalla «Sincronización»). */
    val status: StateFlow<SyncStatus>

    /** Cada vez que la bandeja se renueva sola: true = llegó del servidor; false = el respaldo por REST no pudo. */
    val inboxRenewals: Flow<Boolean>

    /** Tirar para actualizar: la bandeja por REST ya y, si el tiempo real esperaba para reconectar, que reconecte ya. */
    suspend fun syncNow(): Result<Unit>
}

/**
 * Lleva los eventos del SSE a Room. Solo corre con la app en primer plano (lo arranca `:app`); en segundo plano manda el
 * push. Los chats abiertos se «vigilan» para refrescar su historial y burbujas.
 *
 * Dice en [status] si está en vivo y cuánto tardan los eventos, y lo deja en el log [LOG_TAG] (`adb logcat -s
 * HubaraSync`) sin datos de clientes. Un evento que no se pudo guardar se anota y la sincronización sigue (antes la
 * excepción salía de [run] y el tiempo real quedaba apagado hasta mandar la app a segundo plano). Mientras no hay tiempo
 * real, la bandeja y los chats abiertos se piden por REST cada [FALLBACK_POLL_MS].
 */
@Singleton
class SyncEngine @Inject constructor(
    private val events: EventStream,
    private val conversations: ConversationRepository,
    private val chats: ChatRepository,
    private val suggestions: SuggestionRepository,
    private val fires: FireRepository,
    private val orders: OrderRepository,
    private val clock: Clock,
) : ServerChanges, LiveSync {
    private val watched = ConcurrentHashMap<SessionId, AtomicInteger>()
    private val fireRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    private val _changes = MutableSharedFlow<String>(extraBufferCapacity = 16, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /**
     * Qué cambió en el servidor, por dominio (`chats`, `fires`, `orders` o el que mande el bus, p. ej. `marketing`): las
     * pantallas del servidor vuelven a pedir las fuentes atadas a ese dominio con `refreshOn`.
     */
    override val changes: SharedFlow<String> = _changes.asSharedFlow()

    private val _status = MutableStateFlow(SyncStatus())
    override val status: StateFlow<SyncStatus> = _status.asStateFlow()

    private val _inboxRenewals = MutableSharedFlow<Boolean>(extraBufferCapacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    override val inboxRenewals: SharedFlow<Boolean> = _inboxRenewals.asSharedFlow()

    /** Marca un chat como abierto mientras la pantalla lo muestre. Cerrar el handle deja de vigilarlo. */
    fun watch(sessionId: SessionId): AutoCloseable {
        watched.getOrPut(sessionId) { AtomicInteger(0) }.incrementAndGet()
        return AutoCloseable { watched[sessionId]?.let { if (it.decrementAndGet() <= 0) watched.remove(sessionId) } }
    }

    fun isWatched(sessionId: SessionId): Boolean = (watched[sessionId]?.get() ?: 0) > 0

    /** Si el tiempo real esperaba para reconectar, que reconecte ya (tirar para actualizar en un chat). */
    fun reconnectNow() = events.reconnectNow()

    /** Corre hasta que se cancele. La bandeja llega completa al conectar el SSE; después, solo lo que cambia. */
    suspend fun run() = coroutineScope {
        launch { refreshOnSignal(fireRequests, FIRE_MIN_GAP_MS) { fires.refresh() } }
        launch { pollWhileDown() }
        // En paralelo: el tiempo real no espera estas dos recargas (en 4G tardaban segundos al volver a la app).
        launch {
            fires.refresh()
            orders.refreshList()
        }
        try {
            events.updates().collect { onUpdate(it) }
        } finally {
            _status.update { it.copy(state = SyncState.STOPPED, connectedSinceMs = null, retryAtMs = null) }
            Log.i(LOG_TAG, "detenida (app en segundo plano o sin sesión)")
        }
    }

    /** Lo que pasa con la conexión: el estado, el log y, si trae un evento, guardarlo. */
    suspend fun onUpdate(update: StreamUpdate) {
        val now = clock.nowMs()
        when (update) {
            is StreamUpdate.Connecting -> {
                _status.update { it.copy(state = SyncState.CONNECTING, retryAtMs = null) }
                Log.i(LOG_TAG, "conectando el tiempo real" + if (update.failures > 0) " (${update.failures} cortes seguidos)" else "")
            }
            StreamUpdate.Open -> {
                _status.update { it.copy(state = SyncState.LIVE, connectedSinceMs = now, retryAtMs = null) }
                Log.i(LOG_TAG, "en vivo")
            }
            is StreamUpdate.Lost -> {
                _status.update {
                    it.copy(
                        state = SyncState.RETRYING, connectedSinceMs = null, cuts = it.cuts + 1,
                        lastError = update.reason, lastErrorMs = now, retryAtMs = now + update.retryInMs,
                    )
                }
                Log.w(LOG_TAG, "se cortó: ${update.reason}; reintento en ${seconds(update.retryInMs)}")
            }
            is StreamUpdate.Received -> {
                val lag = update.sentAtMs?.let { (now - it).coerceAtLeast(0) }
                _status.update {
                    it.copy(
                        lastEventMs = now, lagMs = lag, maxLagMs = if (lag == null) it.maxLagMs else maxOf(it.maxLagMs ?: 0L, lag),
                        events = it.events + 1, bytes = it.bytes + update.bytes,
                    )
                }
                try {
                    handle(update.event)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    _status.update { it.copy(lastError = "no se pudo guardar: ${e.javaClass.simpleName}", lastErrorMs = now) }
                    Log.e(LOG_TAG, "no se pudo guardar un evento; sigue en vivo", e)
                    return
                }
                describe(update.event)?.let { what ->
                    Log.i(LOG_TAG, "$what, ${kilobytes(update.bytes.toLong())}, retraso ${lag?.let { "$it ms" } ?: "?"}, guardado en ${clock.nowMs() - now} ms")
                }
            }
        }
    }

    suspend fun handle(event: ServerEvent) {
        domains(event).forEach { _changes.emit(it) }
        when (event) {
            is ServerEvent.SessionsSnapshot -> {
                conversations.applySnapshot(event.conversations)
                _inboxRenewals.tryEmit(true)
                fireRequests.tryEmit(Unit)
            }
            is ServerEvent.SessionsDelta -> {
                conversations.applyChanges(event.conversations, event.removed)
                _inboxRenewals.tryEmit(true)
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

    override suspend fun syncNow(): Result<Unit> {
        events.reconnectNow()
        fireRequests.tryEmit(Unit)
        return refreshInbox("a mano")
    }

    /** Respaldo: sin tiempo real, la bandeja y los chats abiertos se piden por REST cada [FALLBACK_POLL_MS]. */
    internal suspend fun pollWhileDown() {
        while (true) {
            delay(FALLBACK_POLL_MS)
            if (_status.value.state == SyncState.LIVE) continue
            refreshInbox("respaldo, sin tiempo real")
            watched.keys.forEach { id ->
                chats.refresh(id)
                suggestions.refresh(id)
            }
            fireRequests.tryEmit(Unit)
        }
    }

    private suspend fun refreshInbox(why: String): Result<Unit> {
        val started = clock.nowMs()
        val result = conversations.refresh()
        val now = clock.nowMs()
        _status.update { it.copy(lastRefreshMs = now, lastRefreshOk = result.isSuccess) }
        _inboxRenewals.tryEmit(result.isSuccess)
        result.onSuccess { Log.i(LOG_TAG, "bandeja por REST ($why) en ${now - started} ms") }
            .onFailure { Log.w(LOG_TAG, "bandeja por REST ($why) falló: ${it.javaClass.simpleName}") }
        return result
    }

    private fun domains(event: ServerEvent): List<String> = when (event) {
        is ServerEvent.SessionsSnapshot, is ServerEvent.SessionsDelta, is ServerEvent.SessionUpdated -> listOf("chats", "fires")
        ServerEvent.OrdersChanged -> listOf("orders")
        is ServerEvent.Unknown -> listOf(event.domain)
    }

    /** Lo que se anota de cada evento de la bandeja (los demás no: serían una línea cada pocos segundos). Sin ids. */
    private fun describe(event: ServerEvent): String? = when (event) {
        is ServerEvent.SessionsSnapshot -> "bandeja completa: ${chatsLabel(event.conversations.size)}"
        is ServerEvent.SessionsDelta -> "cambios: ${chatsLabel(event.conversations.size)}" + if (event.removed.isNotEmpty()) ", ${event.removed.size} fuera" else ""
        else -> null
    }

    companion object {
        const val FIRE_MIN_GAP_MS = 1_500L

        /** Cada cuánto se pide la bandeja por REST mientras no hay tiempo real. */
        const val FALLBACK_POLL_MS = 30_000L

        /** `adb logcat -s HubaraSync`: cómo va el tiempo real en el teléfono. */
        const val LOG_TAG = "HubaraSync"
    }
}

private fun chatsLabel(count: Int) = if (count == 1) "1 chat" else "$count chats"

private fun seconds(ms: Long) = "${(ms + 999) / 1000} s"

private fun kilobytes(bytes: Long) = if (bytes < 1024) "$bytes B" else String.format(Locale.ROOT, "%.1f KB", bytes / 1024.0)

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
