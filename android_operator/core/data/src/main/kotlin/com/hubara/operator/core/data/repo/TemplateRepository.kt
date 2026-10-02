package com.hubara.operator.core.data.repo

import com.hubara.operator.core.data.safeCall
import com.hubara.operator.core.model.Template
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.toDomain
import javax.inject.Inject
import javax.inject.Singleton

/** Plantillas aprobadas para reactivar una conversación. Cambian poco: se guardan en memoria. */
@Singleton
class TemplateRepository @Inject constructor(private val api: OperatorApi) {
    @Volatile private var cache: List<Template>? = null

    suspend fun list(): Result<List<Template>> = cache?.let { Result.success(it) }
        ?: safeCall { api.templates().templates.map { it.toDomain() }.also { cache = it } }
}
