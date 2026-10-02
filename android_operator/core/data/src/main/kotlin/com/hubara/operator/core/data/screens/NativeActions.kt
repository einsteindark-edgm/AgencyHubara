package com.hubara.operator.core.data.screens

import kotlinx.serialization.json.JsonObject

/** Lo que resultó de una acción nativa: nada que avisar, un aviso, un enlace que abrir o un error para el operador. */
sealed interface NativeOutcome {
    data object Done : NativeOutcome
    data class Message(val text: String) : NativeOutcome
    data class OpenUrl(val url: String) : NativeOutcome
    data class Failed(val text: String) : NativeOutcome
}

/**
 * Las acciones que hace la app con lo suyo (`{"type": "native", "name": "send_tool", "args": {…}}`): el outbox con
 * deshacer, tomar o devolver la conversación, ocultar un incendio, cerrar sesión. Los nombres y sus argumentos están en
 * `Catalog.nativeActions`; la implementación vive en `:app` (que ve todos los módulos).
 */
fun interface NativeActions {
    suspend fun run(name: String, args: JsonObject): NativeOutcome
}
