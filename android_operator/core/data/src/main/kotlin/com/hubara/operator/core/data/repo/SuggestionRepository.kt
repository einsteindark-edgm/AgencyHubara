package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.database.SuggestionDao
import com.hubara.operator.core.database.SuggestionSetEntity
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.SuggestionSet
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.SuggestionsDto
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * Lo que sabe hacer esta app con una burbuja (`?features=`): `open_screen` = abrir la pantalla del servidor que pida
 * («Crear pedido» → `crear_pedido`). Sin esto el backend no le manda esas burbujas: una app vieja las enviaría como
 * una tool que no existe.
 */
const val FEATURES = "open_screen"

/** Las burbujas vigentes. Las decide el servidor (hoy por reglas; con Jev, más adelante). */
@Singleton
class SuggestionRepository @Inject constructor(
    private val api: OperatorApi,
    private val dao: SuggestionDao,
) {
    fun observe(sessionId: SessionId): Flow<SuggestionSet?> = dao.observe(sessionId.raw).map { row ->
        row?.let { runCatching { OperatorJson.decodeFromString(SuggestionsDto.serializer(), it.json).toDomain() }.getOrNull() }
    }

    suspend fun refresh(sessionId: SessionId): Result<Unit> = safeCall {
        val dto = api.suggestions(sessionId.raw, features = FEATURES)
        val current = dao.get(sessionId.raw)
        // Una respuesta más vieja que la guardada no pisa la nueva.
        if (current == null || dto.version >= current.version) {
            dao.upsert(SuggestionSetEntity(sessionId.raw, dto.version, OperatorJson.encodeToString(SuggestionsDto.serializer(), dto)))
        }
    }
}
