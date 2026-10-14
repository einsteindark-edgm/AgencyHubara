package com.hubara.operator.core.model

import kotlinx.serialization.Serializable

/**
 * Un envío del bot (o del operador desde la app) que no es texto, con el mensaje ENTERO que leyó el cliente:
 * el catálogo con su texto y sus productos, los botones de respuesta, el formulario de envío, el resumen del
 * pedido, el botón con enlace, las fotos, la tarjeta de contacto.
 *
 * Lo arma el servidor (`event` del historial, `chats/shared/chat_events.py`): [label] e [icon] también, así que
 * un componente nuevo se ve bien sin publicar otra versión de la app. Caso 2026-10-13: el saludo iba dentro del
 * texto del catálogo y la app solo decía «El bot envió el catálogo con 2 productos», en gris.
 */
@Serializable
data class MessageCard(
    /** El `component_kind` del backend (`products_list`, `order_confirmation`…): solo para pruebas y registros. */
    val component: String,
    /** Qué fue, en palabras del operador: «Catálogo», «Resumen para confirmar»… */
    val label: String,
    val icon: CardIcon = CardIcon.MESSAGE,
    /** La categoría de un catálogo por categoría, «por texto» si el formulario no salió… */
    val title: String? = null,
    /** El texto que leyó el cliente, entero y con el formato de WhatsApp (`*negrita*`). */
    val text: String? = null,
    /** Filas de una lista: producto y precio, categoría y cuántos productos, asesor y teléfono. */
    val items: List<CardItem> = emptyList(),
    /** Los botones que pudo tocar el cliente (el que tocó, marcado). */
    val buttons: List<CardButton> = emptyList(),
    /** El botón que abre la lista o el formulario en su WhatsApp («Completar datos»). */
    val action: String? = null,
    val link: CardLink? = null,
    val images: List<CardImage> = emptyList(),
)

@Serializable
data class CardItem(val title: String, val detail: String? = null)

@Serializable
data class CardButton(val title: String, val touched: Boolean = false)

@Serializable
data class CardLink(val label: String, val url: String)

@Serializable
data class CardImage(val url: String, val caption: String? = null)

/** Ícono de la tarjeta: llave de una lista cerrada que manda el servidor. Una que no conozca la app = [MESSAGE]. */
@Serializable
enum class CardIcon {
    CATALOG, LIST, BUTTONS, LINK, PHOTO, FORM, ORDER, CONTACT, MESSAGE;

    companion object {
        fun fromKey(key: String?): CardIcon = entries.firstOrNull { it.name.equals(key, ignoreCase = true) } ?: MESSAGE
    }
}

/** Un tramo del texto de WhatsApp con su formato. */
data class WaSpan(val text: String, val bold: Boolean = false, val italic: Boolean = false, val strike: Boolean = false)

/**
 * El formato de WhatsApp (`*negrita*`, `_cursiva_`, `~tachado~`), igual que en el panel web (`waText.tsx`): la
 * marca abre al inicio o después de algo que no es letra ni número, no empieza ni termina con espacio, no cruza un
 * salto de línea y cierra antes de algo que no es letra ni número. «2 * 3» y «nombre_apellido@…» quedan igual.
 */
object WaFormat {
    private val MARK = Regex("""(^|[^\p{L}\p{N}])([*_~])(?!\s)([^\n]*?\S)\2(?=$|[^\p{L}\p{N}])""")

    fun spans(text: String): List<WaSpan> {
        val out = mutableListOf<WaSpan>()
        var last = 0
        for (match in MARK.findAll(text)) {
            val (before, mark, inner) = match.destructured
            val start = match.range.first + before.length
            if (start > last) out += WaSpan(text.substring(last, start))
            out += WaSpan(inner, bold = mark == "*", italic = mark == "_", strike = mark == "~")
            last = match.range.last + 1
        }
        if (last < text.length) out += WaSpan(text.substring(last))
        return out
    }
}
