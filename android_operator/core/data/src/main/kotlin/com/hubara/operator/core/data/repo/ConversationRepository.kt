package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.mapping.toDomain
import com.hubara.operator.core.data.mapping.toEntity
import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.database.ConversationDao
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** La bandeja. La lista completa llega por REST y al conectar el SSE; después el SSE manda solo lo que cambió. */
@Singleton
class ConversationRepository @Inject constructor(
    private val api: OperatorApi,
    private val dao: ConversationDao,
) {
    fun observeInbox(): Flow<List<Conversation>> = dao.observeAll().map { rows -> rows.mapNotNull { it.toDomain() } }

    suspend fun refresh(): Result<Unit> = safeCall {
        applySnapshot(api.sessions().sessions.mapNotNull { it.toDomain() })
    }

    suspend fun applySnapshot(conversations: List<Conversation>) {
        dao.replaceAll(conversations.map { it.toEntity() })
    }

    suspend fun applyChanges(changed: List<Conversation>, removed: List<SessionId>) {
        dao.applyChanges(changed.map { it.toEntity() }, removed.map { it.raw })
    }
}
