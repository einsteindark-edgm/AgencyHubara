package com.hubara.operator.core.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** La misma acción que usaría el bot: nombre de la tool y sus argumentos. */
@Serializable
data class ActionRef(val name: String, val args: JsonObject = JsonObject(emptyMap()))

enum class Prominence { PRIMARY, NORMAL }

/** El color de la burbuja: [ORDER] = «Crear pedido», que se tiene que ver de un vistazo. */
enum class SuggestionTone { NORMAL, ORDER }

data class Suggestion(
    val id: String,
    val label: String,
    val prominence: Prominence,
    val action: ActionRef,
    val editable: Boolean,
    val tone: SuggestionTone = SuggestionTone.NORMAL,
    /** La pantalla del servidor que abre en vez de mandar la acción («Crear pedido» → `crear_pedido`), o null. */
    val opens: String? = null,
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
