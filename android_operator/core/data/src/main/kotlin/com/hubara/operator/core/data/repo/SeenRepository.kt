package com.hubara.operator.core.data.repo

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.hubara.operator.core.database.ConversationDao
import com.hubara.operator.core.model.SeenCounts
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.OperatorJson
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable

private val Context.seenStore: DataStore<Preferences> by preferencesDataStore(name = "seen")

/**
 * Lo que el operador ya vio de cada chat en ESTE teléfono (el dashboard web lo guarda en su
 * navegador). Con eso la bandeja cuenta los no leídos a partir del total que da el backend.
 */
@Singleton
class SeenRepository @Inject constructor(
    @ApplicationContext private val context: Context,
    private val conversations: ConversationDao,
) {
    private val key = stringPreferencesKey("seen_json")

    val counts: Flow<SeenCounts> = context.seenStore.data.map { prefs -> decode(prefs[key]) }.distinctUntilChanged()

    suspend fun update(transform: (SeenCounts) -> SeenCounts) {
        context.seenStore.edit { prefs ->
            val current = decode(prefs[key])
            val next = transform(current)
            if (next !== current) prefs[key] = OperatorJson.encodeToString(SeenJson.serializer(), SeenJson(next.baseline, next.seen))
        }
    }

    /** Mientras el chat está a la vista, lo que escribe el cliente queda visto (como tenerlo abierto en WhatsApp). */
    suspend fun keepSeen(sessionId: SessionId) {
        conversations.observe(sessionId.raw).filterNotNull().map { it.inboundCount }.distinctUntilChanged()
            .collect { count -> update { it.markSeen(sessionId, count) } }
    }

    private fun decode(raw: String?): SeenCounts = raw
        ?.let { runCatching { OperatorJson.decodeFromString(SeenJson.serializer(), it) }.getOrNull() }
        ?.let { SeenCounts(it.baseline, it.seen) }
        ?: SeenCounts()

    @Serializable
    private data class SeenJson(val baseline: Boolean = false, val seen: Map<String, Int> = emptyMap())
}
