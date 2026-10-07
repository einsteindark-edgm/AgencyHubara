package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.database.FireDao
import com.hubara.operator.core.database.FireEntity
import com.hubara.operator.core.database.HiddenFireEntity
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireOrdering
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.dto.FireDto
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map

/** Incendios de chats y órdenes. El radar son los graves que el operador no ocultó. */
@Singleton
class FireRepository @Inject constructor(
    private val api: OperatorApi,
    private val dao: FireDao,
    private val clock: Clock,
) {
    fun observeFeed(): Flow<List<Fire>> = dao.observeAll().map { rows -> rows.mapNotNull { it.toDomain() }.sortedWith(FireOrdering.FEED) }

    fun observeRadar(): Flow<List<Fire>> = combine(observeFeed(), dao.observeHidden()) { fires, hidden ->
        FireOrdering.radar(fires, hidden.mapNotNull(FireId::parse).toSet())
    }

    suspend fun refresh(): Result<Unit> = safeCall {
        val dto = api.fires()
        // Se guarda el DTO tal cual: el mapeo al dominio vive en un solo lugar (`toDomain`).
        dao.replaceAll(dto.fires.map { f ->
            FireEntity(
                fireId = f.fireId,
                severity = f.toDomain(dto.decidedBy)?.severity?.ordinal ?: 2,
                updatedMs = f.updatedMs,
                json = OperatorJson.encodeToString(StoredFire.serializer(), StoredFire(dto.decidedBy, f)),
            )
        })
    }

    /** Ocultar no resuelve: sigue en Incendios, solo sale del radar. */
    suspend fun hide(id: FireId) = dao.hide(HiddenFireEntity(id.raw, clock.nowMs()))

    private fun FireEntity.toDomain(): Fire? = runCatching {
        val stored = OperatorJson.decodeFromString(StoredFire.serializer(), json)
        stored.fire.toDomain(stored.decidedBy)
    }.getOrNull()
}

@kotlinx.serialization.Serializable
internal data class StoredFire(val decidedBy: String, val fire: FireDto)
