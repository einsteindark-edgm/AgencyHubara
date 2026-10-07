package com.hubara.operator.core.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** La misma acción que usaría el bot: nombre de la tool y sus argumentos. */
@Serializable
data class ActionRef(val name: String, val args: JsonObject = JsonObject(emptyMap()))

enum class Prominence { PRIMARY, NORMAL }

data class Suggestion(
    val id: String,
    val label: String,
    val prominence: Prominence,
    val action: ActionRef,
    val editable: Boolean,
)

/** Las burbujas vigentes de una conversación. `version` descarta respuestas viejas. */
data class SuggestionSet(
    val sessionId: SessionId,
    val version: Long,
    val decidedBy: String,
    val stage: String?,
    val windowOpen: Boolean,
    val humanInControl: Boolean,
    val suggestions: List<Suggestion>,
) {
    /** Aplica una respuesta nueva solo si no es más vieja que la que ya está. */
    fun newerOrSame(candidate: SuggestionSet): SuggestionSet =
        if (candidate.version >= version) candidate else this
}
