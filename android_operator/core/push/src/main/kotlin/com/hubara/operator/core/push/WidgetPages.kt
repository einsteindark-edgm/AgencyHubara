package com.hubara.operator.core.push

import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.network.dto.HotSaleDto
import com.hubara.operator.core.network.dto.HumanDto
import kotlinx.serialization.Serializable

/** Las páginas del widget, en el orden en que se deslizan. */
enum class WidgetPageKind(val title: String, val empty: String) {
    HOT("Ventas calientes", "Nada por cerrar ahora."),
    FIRES("Incendios", "Sin incendios. Todo en calma."),
    HUMAN("Humano", "Nadie atiende un chat ahora."),
}

/**
 * Una fila del widget: quién y en qué va, sin mensajes (el widget está en la pantalla de inicio). [link] es el enlace
 * `hubara://` que abre al tocarla; la app lo vuelve a validar al llegar (`DeepLinks.parse`).
 */
@Serializable
data class WidgetRow(val title: String, val detail: String, val link: String?, val urgent: Boolean = false) {
    val line: String get() = listOf(title, detail).filter { it.isNotBlank() }.joinToString(" · ")
}

/** Hasta [MAX_WIDGET_ROWS] filas y cuántas hay en total (el número del encabezado). */
@Serializable
data class WidgetPage(val rows: List<WidgetRow> = emptyList(), val total: Int = 0)

/** Lo que muestra el widget. Una página null nunca cargó (recién instalada o tras cerrar sesión). */
data class WidgetPages(val hot: WidgetPage? = null, val fires: WidgetPage? = null, val human: WidgetPage? = null) {
    operator fun get(kind: WidgetPageKind): WidgetPage? = when (kind) {
        WidgetPageKind.HOT -> hot
        WidgetPageKind.FIRES -> fires
        WidgetPageKind.HUMAN -> human
    }
}

const val MAX_WIDGET_ROWS = 3

private fun chatLink(session: String) = "hubara://chat/$session"

/** Etapa del embudo en palabras cortas, para el widget. */
fun widgetStage(stage: String?): String = when (stage) {
    "etapa_datos_envio" -> "datos de envío"
    "etapa_cierre" -> "cierre"
    else -> "en curso"
}

private fun page(rows: List<WidgetRow>, total: Int = rows.size) = WidgetPage(rows.take(MAX_WIDGET_ROWS), total)

/** Ventas que el bot está por cerrar: quién, etapa, producto y si hay riesgo. */
fun hotPage(list: List<HotSaleDto>): WidgetPage = page(list.map { row ->
    WidgetRow(
        title = row.name?.takeIf { it.isNotBlank() } ?: "Cliente",
        detail = listOfNotNull(widgetStage(row.stage), row.product, if (row.risk) "RIESGO" else null).joinToString(" · "),
        link = chatLink(row.sessionId),
        urgent = row.risk,
    )
})

/**
 * Incendios, los graves primero. Sin el subtítulo («12 min sin respuesta»): el widget no se repinta cada minuto y la
 * espera quedaría vieja. Abre el chat; un pedido sin chat conocido, su ficha.
 */
fun firesPage(fires: List<Fire>): WidgetPage = page(fires.sortedBy { it.severity.ordinal }.map { fire ->
    val order = (fire.subject as? FireSubject.Order)?.orderId
    WidgetRow(
        title = fire.title,
        detail = listOfNotNull(
            when (fire.severity) {
                Severity.GRAVE -> "grave"
                Severity.HOY -> "hoy"
                Severity.ESPERA -> "en espera"
            },
            if (fire.gettingWorse) "empeora" else null,
        ).joinToString(" · "),
        link = fire.subject.sessionId?.let { chatLink(it.raw) } ?: order?.let { "hubara://order/${it.raw}" },
        urgent = fire.severity == Severity.GRAVE,
    )
})

/** Lo que atiende una persona: primero quien espera respuesta (así lo ordena el backend). */
fun humanPage(dto: HumanDto): WidgetPage = page(
    dto.human.map { chat ->
        WidgetRow(
            title = chat.name?.takeIf { it.isNotBlank() } ?: "Cliente",
            detail = if (chat.unanswered > 0) "${chat.unanswered} sin responder" else "al día",
            link = chatLink(chat.sessionId),
            urgent = chat.unanswered > 0,
        )
    },
    total = maxOf(dto.total, dto.human.size),
)
