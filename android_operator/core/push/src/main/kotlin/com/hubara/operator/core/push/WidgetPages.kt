package com.hubara.operator.core.push

import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.network.dto.HotSaleDto
import com.hubara.operator.core.network.dto.HumanDto
import kotlinx.serialization.Serializable

/**
 * Las pestañas del widget, en orden: lo más urgente primero. [link] es lo que abre el widget compacto al tocar el
 * contador (la pestaña de la app; Humano y Ventas viven en la bandeja).
 */
enum class WidgetPageKind(val title: String, val empty: String, val link: String) {
    FIRES("Incendios", "Sin incendios. Todo en calma.", "hubara://tab/incendios"),
    HUMAN("Humano", "Nadie atiende un chat ahora.", "hubara://tab/chats"),
    HOT("Ventas", "Nada por cerrar ahora.", "hubara://tab/chats"),
}

/** El color de la etiqueta y de las iniciales: grave (rojo), hoy o riesgo (ámbar), lo demás neutro. */
enum class WidgetTone { DANGER, WARNING, NEUTRAL }

/** Una hora FIJA con su palabra («desde 10:42», «último 9:15»): el widget la escribe al pintar (`listTimeLabel`). */
@Serializable
data class WidgetSince(val label: String, val ms: Long)

/**
 * Una fila del widget: quién y en qué va, sin mensajes (está en la pantalla de inicio). [link] es el `hubara://` que
 * abre al tocarla; la app lo vuelve a validar al llegar (`DeepLinks.parse`). Sin [initials] (un pedido sin cliente
 * conocido) va un ícono.
 */
@Serializable
data class WidgetRow(
    val title: String,
    val detail: String,
    val link: String?,
    val initials: String? = null,
    val tone: WidgetTone = WidgetTone.NEUTRAL,
    val tag: String? = null,
    val since: WidgetSince? = null,
)

/** Hasta [MAX_WIDGET_ROWS] filas, cuántas hay en total (el número de la pestaña) y cuándo se trajo. */
@Serializable
data class WidgetPage(val rows: List<WidgetRow> = emptyList(), val total: Int = 0, val updatedMs: Long = 0)

/** Lo que muestra el widget. Una página null nunca cargó (recién instalada o tras cerrar sesión). */
data class WidgetPages(val hot: WidgetPage? = null, val fires: WidgetPage? = null, val human: WidgetPage? = null) {
    operator fun get(kind: WidgetPageKind): WidgetPage? = when (kind) {
        WidgetPageKind.HOT -> hot
        WidgetPageKind.FIRES -> fires
        WidgetPageKind.HUMAN -> human
    }
}

/** La lista del widget se desplaza: 10 filas alcanzan para un día normal sin inflar lo que se guarda. */
const val MAX_WIDGET_ROWS = 10

private fun chatLink(session: String) = "hubara://chat/$session"

/** Iniciales de un nombre («Sofía Prueba» → «SP»); null si no hay nombre de verdad. */
internal fun initialsOf(name: String?): String? =
    name?.split(' ')?.filter { it.isNotBlank() && it.first().isLetter() }?.take(2)
        ?.joinToString("") { it.first().uppercase() }?.ifEmpty { null }

/** Etapa del embudo en palabras cortas, para el widget. */
fun widgetStage(stage: String?): String = when (stage) {
    "etapa_datos_envio" -> "datos de envío"
    "etapa_cierre" -> "cierre"
    else -> "en curso"
}

private fun page(rows: List<WidgetRow>, nowMs: Long, total: Int = rows.size) =
    WidgetPage(rows.take(MAX_WIDGET_ROWS), maxOf(total, rows.size), nowMs)

/** Ventas que el bot está por cerrar: quién, etapa, producto y si hay riesgo. */
fun hotPage(list: List<HotSaleDto>, nowMs: Long): WidgetPage = page(list.map { row ->
    val name = row.name?.takeIf { it.isNotBlank() }
    WidgetRow(
        title = name ?: "Cliente",
        detail = listOfNotNull(widgetStage(row.stage), row.product).joinToString(" · "),
        link = chatLink(row.sessionId),
        initials = initialsOf(name),
        tone = if (row.risk) WidgetTone.WARNING else WidgetTone.NEUTRAL,
        tag = if (row.risk) "Riesgo" else null,
        since = row.updatedMs.takeIf { it > 0 }?.let { WidgetSince("último", it) },
    )
}, nowMs)

/**
 * Incendios, los graves primero. Del subtítulo se quita la espera («12 min sin respuesta»): el widget no se repinta
 * cada minuto y quedaría vieja; en su lugar va la hora fija del último mensaje. Abre el chat; un pedido sin chat
 * conocido, su ficha. [names]: el nombre de cada sesión que ya está en la bandeja (para las iniciales).
 */
fun firesPage(fires: List<Fire>, names: Map<String, String?>, nowMs: Long): WidgetPage = page(fires.sortedBy { it.severity.ordinal }.map { fire ->
    val session = fire.subject.sessionId
    val order = (fire.subject as? FireSubject.Order)?.orderId
    val chat = fire.subject is FireSubject.Chat
    WidgetRow(
        title = fire.title,
        detail = (fire.subtitle.split(" · ").filterNot { "sin respuesta" in it } + listOfNotNull(if (fire.gettingWorse) "empeora" else null))
            .filter { it.isNotBlank() }.joinToString(" · "),
        link = session?.let { chatLink(it.raw) } ?: order?.let { "hubara://order/${it.raw}" },
        initials = session?.let { initialsOf(names[it.raw]) },
        tone = when (fire.severity) {
            Severity.GRAVE -> WidgetTone.DANGER
            Severity.HOY -> WidgetTone.WARNING
            Severity.ESPERA -> WidgetTone.NEUTRAL
        },
        tag = when (fire.severity) {
            Severity.GRAVE -> "Grave"
            Severity.HOY -> "Hoy"
            Severity.ESPERA -> "En espera"
        },
        since = if (chat && fire.updatedMs > 0) WidgetSince("último", fire.updatedMs) else null,
    )
}, nowMs)

/** Lo que atiende una persona: primero quien espera respuesta (así lo ordena el backend), con la hora desde que espera. */
fun humanPage(dto: HumanDto, nowMs: Long): WidgetPage = page(
    dto.human.map { chat ->
        val name = chat.name?.takeIf { it.isNotBlank() }
        val waiting = chat.unanswered > 0
        WidgetRow(
            title = name ?: "Cliente",
            detail = if (waiting) "${chat.unanswered} sin responder" else "Al día",
            link = chatLink(chat.sessionId),
            initials = initialsOf(name),
            tone = if (waiting) WidgetTone.DANGER else WidgetTone.NEUTRAL,
            tag = if (waiting) "Espera" else null,
            since = if (waiting) chat.waitingSinceMs?.let { WidgetSince("desde", it) }
            else chat.lastInboundMs?.let { WidgetSince("último", it) },
        )
    },
    nowMs,
    total = dto.total,
)
