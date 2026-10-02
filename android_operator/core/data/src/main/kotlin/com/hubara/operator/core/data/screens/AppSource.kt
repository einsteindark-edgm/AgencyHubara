package com.hubara.operator.core.data.screens

import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.JsonElement

/**
 * Una fuente de datos del TELÉFONO para las pantallas del servidor (`"data": {"chats": {"app": "conversations"}}`): lo
 * que ya vive en la app y se mantiene al día solo (Room con el SSE, lo no leído de este teléfono). Entrega JSON listo
 * para enlazar; [refresh] la pide de nuevo al backend (tirar para actualizar). Los nombres y sus campos están en
 * `Catalog.appSources` (`:core:sdui`); cada una se registra en `ScreensModule`.
 */
interface AppSource {
    fun observe(params: Map<String, String>): Flow<JsonElement>

    suspend fun refresh(params: Map<String, String>): Result<Unit> = Result.success(Unit)
}
