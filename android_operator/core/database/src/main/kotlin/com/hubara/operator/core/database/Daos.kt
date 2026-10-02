package com.hubara.operator.core.database

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface ConversationDao {
    @Query("SELECT * FROM conversations ORDER BY lastUpdatedMs DESC")
    fun observeAll(): Flow<List<ConversationEntity>>

    @Query("SELECT * FROM conversations WHERE sessionId = :sessionId")
    fun observe(sessionId: String): Flow<ConversationEntity?>

    @Query("SELECT * FROM conversations WHERE sessionId = :sessionId")
    suspend fun get(sessionId: String): ConversationEntity?

    @Upsert
    suspend fun upsert(rows: List<ConversationEntity>)

    @Query("DELETE FROM conversations WHERE sessionId NOT IN (:keep)")
    suspend fun deleteAllExcept(keep: List<String>)

    @Query("UPDATE conversations SET route = :route WHERE sessionId = :sessionId")
    suspend fun setRoute(sessionId: String, route: String)

    /** La bandeja manda la lista completa: se conserva la ventana de 24 h que llegó con cada detalle. */
    @Transaction
    suspend fun replaceAll(rows: List<ConversationEntity>) {
        val windows = rows.associate { it.sessionId to get(it.sessionId)?.windowExpiresAtMs }
        upsert(rows.map { it.copy(windowExpiresAtMs = it.windowExpiresAtMs ?: windows[it.sessionId]) })
        deleteAllExcept(rows.map { it.sessionId })
    }
}

@Dao
interface MessageDao {
    @Query("SELECT * FROM messages WHERE sessionId = :sessionId ORDER BY position ASC")
    fun observe(sessionId: String): Flow<List<MessageEntity>>

    @Query("DELETE FROM messages WHERE sessionId = :sessionId")
    suspend fun deleteSession(sessionId: String)

    @Upsert
    suspend fun upsert(rows: List<MessageEntity>)

    @Transaction
    suspend fun replaceSession(sessionId: String, rows: List<MessageEntity>) {
        deleteSession(sessionId)
        upsert(rows)
    }
}

@Dao
interface OutboxDao {
    @Upsert
    suspend fun upsert(row: OutboxEntity)

    @Query("SELECT * FROM outbox WHERE clientActionId = :id")
    suspend fun get(id: String): OutboxEntity?

    @Query("SELECT * FROM outbox WHERE sessionId = :sessionId AND state != 'sent' ORDER BY createdMs ASC")
    fun observeUnsent(sessionId: String): Flow<List<OutboxEntity>>

    @Query("UPDATE outbox SET state = :state, error = :error WHERE clientActionId = :id")
    suspend fun setState(id: String, state: String, error: String? = null)

    @Query("DELETE FROM outbox WHERE clientActionId = :id")
    suspend fun delete(id: String)

    /** Toma el envío para mandarlo. 0 = ya no está disponible (se deshizo o lo tomó otro intento). */
    @Query("UPDATE outbox SET state = 'sending' WHERE clientActionId = :id AND state IN ('pending_undo', 'queued')")
    suspend fun claim(id: String): Int

    /** Deshace solo si todavía no se empezó a mandar. 0 = demasiado tarde. */
    @Query("DELETE FROM outbox WHERE clientActionId = :id AND state = 'pending_undo'")
    suspend fun undo(id: String): Int

    @Query("DELETE FROM outbox WHERE state = 'sent' AND createdMs < :beforeMs")
    suspend fun pruneSent(beforeMs: Long)
}

@Dao
interface DraftDao {
    @Query("SELECT * FROM drafts WHERE sessionId = :sessionId")
    suspend fun get(sessionId: String): DraftEntity?

    @Upsert
    suspend fun upsert(row: DraftEntity)

    @Query("DELETE FROM drafts WHERE sessionId = :sessionId")
    suspend fun delete(sessionId: String)
}

@Dao
interface SuggestionDao {
    @Query("SELECT * FROM suggestion_sets WHERE sessionId = :sessionId")
    fun observe(sessionId: String): Flow<SuggestionSetEntity?>

    @Query("SELECT * FROM suggestion_sets WHERE sessionId = :sessionId")
    suspend fun get(sessionId: String): SuggestionSetEntity?

    @Upsert
    suspend fun upsert(row: SuggestionSetEntity)
}

@Dao
interface FireDao {
    @Query("SELECT * FROM fires ORDER BY severity ASC, updatedMs ASC")
    fun observeAll(): Flow<List<FireEntity>>

    @Query("SELECT fireId FROM hidden_fires")
    fun observeHidden(): Flow<List<String>>

    @Upsert
    suspend fun upsert(rows: List<FireEntity>)

    @Query("DELETE FROM fires WHERE fireId NOT IN (:keep)")
    suspend fun deleteAllExcept(keep: List<String>)

    @Query("DELETE FROM fires")
    suspend fun deleteAll()

    @Upsert
    suspend fun hide(row: HiddenFireEntity)

    /** Los ocultos que ya no están en el feed se olvidan: si vuelven, vuelven a avisar. */
    @Query("DELETE FROM hidden_fires WHERE fireId NOT IN (SELECT fireId FROM fires)")
    suspend fun pruneHidden()

    @Transaction
    suspend fun replaceAll(rows: List<FireEntity>) {
        if (rows.isEmpty()) deleteAll() else {
            upsert(rows)
            deleteAllExcept(rows.map { it.fireId })
        }
        pruneHidden()
    }
}
