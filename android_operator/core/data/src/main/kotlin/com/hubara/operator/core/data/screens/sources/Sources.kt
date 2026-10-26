package com.hubara.operator.core.data.screens.sources

import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.SeenRepository
import com.hubara.operator.core.data.repo.TemplateRepository
import com.hubara.operator.core.data.screens.AppSource
import com.hubara.operator.core.data.sync.LiveSync
import com.hubara.operator.core.data.sync.SyncState
import com.hubara.operator.core.model.SessionId
import javax.inject.Inject
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.onStart
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** La bandeja (Room, al día por el SSE) con lo no leído de este teléfono. */
class ConversationsSource @Inject constructor(
    private val repo: ConversationRepository,
    private val seen: SeenRepository,
    private val sync: LiveSync,
) : AppSource {
    override fun observe(params: Map<String, String>): Flow<JsonElement> {
        // Línea base con la primera bandeja que llega: lo que ya había cuenta como visto (como el dashboard web).
        val inbox = repo.observeInbox().onEach { list -> if (list.isNotEmpty()) seen.update { it.withBaseline(list) } }
        return combine(inbox, seen.counts) { list, counts -> JsonArray(list.map { conversationJson(it, counts.unseen(it)) }) }
    }

    /** Tirar para actualizar: la bandeja por REST ya y, si el tiempo real esperaba para reconectar, reconecta. */
    override suspend fun refresh(params: Map<String, String>): Result<Unit> = sync.syncNow()

    override fun renewals(params: Map<String, String>): Flow<Boolean> = sync.inboxRenewals
}

/**
 * La sincronización de este teléfono (pantalla «Sincronización»). Se renueva cada segundo mientras se mira, para que
 * «hace 7 s» avance; en pausa (app en segundo plano) no. Tirar para actualizar sincroniza ya.
 */
class SyncSource @Inject constructor(private val sync: LiveSync, private val clock: Clock) : AppSource {
    @OptIn(ExperimentalCoroutinesApi::class)
    override fun observe(params: Map<String, String>): Flow<JsonElement> = sync.status.flatMapLatest { status ->
        flow {
            while (true) {
                emit(syncJson(status, clock.nowMs()))
                if (status.state == SyncState.STOPPED) break
                delay(1_000)
            }
        }
    }

    override suspend fun refresh(params: Map<String, String>): Result<Unit> = sync.syncNow()
}

/** Los incendios (graves primero). Los guarda Room; el sampler del SSE los recarga. */
class FiresSource @Inject constructor(private val repo: FireRepository) : AppSource {
    override fun observe(params: Map<String, String>): Flow<JsonElement> = repo.observeFeed().map { list -> JsonArray(list.map(::fireJson)) }

    override suspend fun refresh(params: Map<String, String>): Result<Unit> = repo.refresh()
}

/** Lo que cuenta el chip del radar: graves que el operador no ocultó. */
class RadarSource @Inject constructor(private val repo: FireRepository) : AppSource {
    override fun observe(params: Map<String, String>): Flow<JsonElement> = repo.observeRadar().map { list -> JsonArray(list.map(::fireJson)) }

    override suspend fun refresh(params: Map<String, String>): Result<Unit> = repo.refresh()
}

/** El encabezado de un chat (params: session). */
class ChatSource @Inject constructor(private val chats: ChatRepository, private val clock: Clock) : AppSource {
    override fun observe(params: Map<String, String>): Flow<JsonElement> {
        val session = params["session"]?.let(SessionId::parse) ?: return emptyFlow()
        return chats.observeChat(session).map { chatJson(it, clock.nowMs()) }
    }

    override suspend fun refresh(params: Map<String, String>): Result<Unit> {
        val session = params["session"]?.let(SessionId::parse) ?: return Result.failure(IllegalArgumentException("sesión inválida"))
        return chats.refresh(session)
    }
}

/** Las plantillas aprobadas para reactivar una conversación (cambian poco: el repositorio las guarda en memoria). */
class TemplatesSource @Inject constructor(private val repo: TemplateRepository) : AppSource {
    private val loaded = MutableStateFlow<JsonElement?>(null)

    override fun observe(params: Map<String, String>): Flow<JsonElement> =
        loaded.onStart { if (loaded.value == null) refresh(params) }.filterNotNull()

    override suspend fun refresh(params: Map<String, String>): Result<Unit> =
        repo.list().onSuccess { list -> loaded.value = JsonArray(list.map(::templateJson)) }.map { }
}

/** Datos de la app para las pantallas: si hay política de privacidad que abrir. */
class AppInfoSource @Inject constructor(private val config: ServerConfigStore) : AppSource {
    override fun observe(params: Map<String, String>): Flow<JsonElement> =
        config.current.map { buildJsonObject { put("privacy", it.privacyUrl != null) } }
}
