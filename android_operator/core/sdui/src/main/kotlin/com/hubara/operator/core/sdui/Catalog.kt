package com.hubara.operator.core.sdui

/** Qué tipo de valor lleva una propiedad. Todo lo de texto acepta `{{ … }}`. */
enum class PropKind {
    /** Texto con plantillas. */
    TEXT,

    /** Verdadero/falso, o una plantilla que se evalúa (`{{item.paid}}`). */
    BOOL,

    /** Un número (dp, columnas, líneas). */
    NUMBER,

    /** Uno de [PropSpec.values] (o una plantilla que dé uno de ellos). */
    ENUM,

    /** El nombre de un ícono de [Catalog.icons]. */
    ICON,

    /** Una plantilla que da una lista (`{{pedidos.orders}}`). */
    LIST,

    /** Lista de opciones `[{ "value": "…", "label": "…" }]` (o una plantilla que la dé). */
    OPTIONS,

    /** Dónde guarda una entrada lo que escribe el operador: `state.<clave>` (vuelve a pedir los datos) o `form.<clave>`. */
    BIND,

    /** Una acción (`{"type": "navigate", …}`), para lo que hace un componente nativo (p. ej. el «+ Más» del chat). */
    ACTION,
}

data class PropSpec(
    val name: String,
    val kind: PropKind,
    val doc: String,
    val required: Boolean = false,
    val values: List<String> = emptyList(),
)

/**
 * Un componente del catálogo. [since] es la versión del catálogo en que apareció: una pantalla que lo usa tiene que
 * declarar `"requires"` igual o mayor, y una app más vieja muestra «Actualiza la app» en vez de una pantalla a medias.
 */
data class ComponentSpec(
    val type: String,
    val doc: String,
    val props: List<PropSpec>,
    val since: Int = 1,
    /** Acepta `children`. */
    val container: Boolean = false,
    /** Acepta `action` (se toca). */
    val tappable: Boolean = false,
    /** La acción es obligatoria (un botón sin acción no sirve). */
    val actionRequired: Boolean = false,
    /** Es una lista: `items`, `as`, `item` y `empty`. */
    val list: Boolean = false,
    /** Lo pinta una pieza nativa de la app (el chat, el aviso de notificaciones), no un componente genérico. */
    val native: Boolean = false,
) {
    fun prop(name: String): PropSpec? = props.firstOrNull { it.name == name }
}

private val TONES = listOf("neutral", "primary", "secondary", "bot", "success", "warning", "danger")
private val TEXT_TONES = listOf("default", "muted", "primary", "bot", "success", "warning", "danger")

private fun text(name: String, doc: String, required: Boolean = false) = PropSpec(name, PropKind.TEXT, doc, required)
private fun bool(name: String, doc: String) = PropSpec(name, PropKind.BOOL, doc)
private fun number(name: String, doc: String) = PropSpec(name, PropKind.NUMBER, doc)
private fun enum(name: String, doc: String, values: List<String>) = PropSpec(name, PropKind.ENUM, doc, values = values)
private fun icon(name: String, doc: String, required: Boolean = false) = PropSpec(name, PropKind.ICON, doc, required)
private fun tone(name: String = "tone", doc: String = "Color con significado.") = enum(name, doc, TONES)

/**
 * El catálogo CERRADO de componentes que esta versión de la app sabe pintar, con los íconos y las acciones. Es la
 * única fuente: de aquí salen la validación de la CI, el esquema JSON para el editor (`screen.schema.json`) y la
 * tabla de la guía. Agregar un componente = agregarlo aquí + pintarlo en `:feature:screens` + subir [VERSION].
 */
object Catalog {
    /** Versión del catálogo que entiende ESTA app. Sube cuando se agrega un componente, un ícono o una acción. */
    const val VERSION = 1

    val components: Map<String, ComponentSpec> = listOf(
        // ── Estructura ───────────────────────────────────────────────────────────────────────────────
        ComponentSpec(
            "column", "Apila sus hijos de arriba abajo.",
            listOf(number("spacing", "Espacio entre hijos en dp (por defecto 8)."), number("padding", "Margen interno en dp."),
                enum("align", "Alineación horizontal.", listOf("start", "center", "end"))),
            container = true,
        ),
        ComponentSpec(
            "row", "Pone sus hijos uno al lado del otro. Un hijo con `\"weight\": 1` ocupa el espacio que sobra.",
            listOf(number("spacing", "Espacio entre hijos en dp (por defecto 8)."),
                enum("align", "Alineación vertical.", listOf("top", "center", "bottom")),
                enum("arrange", "Cómo reparte el espacio.", listOf("start", "center", "end", "between")),
                bool("scroll", "Se desliza de lado si no cabe."), bool("wrap", "Pasa a otra línea si no cabe.")),
            container = true,
        ),
        ComponentSpec(
            "grid", "Cuadrícula de N columnas (para tarjetas de cifras).",
            listOf(number("columns", "Columnas (por defecto 2)."), number("spacing", "Espacio en dp (por defecto 8).")),
            container = true,
        ),
        ComponentSpec(
            "card", "Tarjeta que agrupa a sus hijos. Se puede tocar.",
            listOf(enum("style", "Relleno tonal, con borde o elevada.", listOf("filled", "outlined", "elevated")),
                tone(doc = "Color del fondo (neutral por defecto)."), number("padding", "Margen interno en dp (por defecto 16).")),
            container = true, tappable = true,
        ),
        ComponentSpec(
            "section", "Un bloque con título (y un enlace opcional a la derecha, con `action` + `action_label`).",
            listOf(text("title", "Título de la sección.", required = true), text("subtitle", "Línea de ayuda debajo."),
                text("action_label", "Texto del enlace de la derecha («Ver todo»).")),
            container = true, tappable = true,
        ),
        ComponentSpec(
            "list", "Repite `item` por cada elemento de `items` (cada uno se lee con el nombre de `as`, por defecto `item`, y su posición con `index`), o muestra sus `children` como renglones fijos (un menú).",
            listOf(PropSpec("items", PropKind.LIST, "La lista: `{{pedidos.orders}}` o una lista escrita tal cual `[{…}, {…}]`."),
                text("key", "Identificador estable de cada elemento (`{{item.id}}`)."),
                enum("style", "Renglones segmentados (por defecto), tarjetas separadas o plana (sin fondo ni separadores, como la bandeja).", listOf("segmented", "cards", "plain")),
                number("limit", "Muestra como mucho N.")),
            list = true, container = true,
        ),
        ComponentSpec("spacer", "Espacio vacío.", listOf(number("size", "Alto en dp (por defecto 16)."))),
        ComponentSpec("divider", "Línea divisoria.", emptyList()),
        // ── Contenido ────────────────────────────────────────────────────────────────────────────────
        ComponentSpec(
            "text", "Un texto.",
            listOf(text("text", "El texto.", required = true),
                enum("style", "Tamaño (por defecto body).", listOf("display", "headline", "title", "subtitle", "body", "label", "caption")),
                bool("emphasis", "Versión enfatizada de Material 3 Expressive (más peso)."),
                enum("tone", "Color.", TEXT_TONES), enum("align", "Alineación.", listOf("start", "center", "end")),
                number("max_lines", "Corta con «…» después de N líneas.")),
        ),
        ComponentSpec(
            "icon", "Un ícono suelto.",
            listOf(icon("name", "Nombre del ícono.", required = true), enum("tone", "Color.", TEXT_TONES), number("size", "Tamaño en dp (por defecto 24).")),
        ),
        ComponentSpec(
            "image", "Una imagen por https.",
            listOf(text("url", "Dirección https de la imagen.", required = true), number("ratio", "Ancho/alto (por defecto 1.5)."),
                enum("shape", "Forma.", listOf("rounded", "square", "circle")), text("description", "Qué muestra (para TalkBack).")),
        ),
        ComponentSpec(
            "avatar", "Círculo con las iniciales de un nombre.",
            listOf(text("name", "Nombre (de ahí salen las iniciales).", required = true), text("seed", "Valor estable para el color (por defecto el nombre)."),
                number("size", "Tamaño en dp (por defecto 40).")),
        ),
        ComponentSpec(
            "tag", "Píldora corta de estado.",
            listOf(text("text", "El texto.", required = true), tone(), icon("icon", "Ícono a la izquierda.")),
        ),
        ComponentSpec(
            "list_item", "El renglón de una lista: ícono o avatar, título, subtítulo, una píldora y un valor a la derecha. Se puede tocar.",
            listOf(text("title", "Primera línea.", required = true), text("subtitle", "Segunda línea."), text("overline", "Línea chica arriba del título."),
                icon("icon", "Ícono a la izquierda."), tone("icon_tone", "Color del ícono."), text("avatar", "Nombre para un avatar a la izquierda (en vez de ícono)."),
                text("avatar_seed", "Valor estable para el color del avatar (por defecto el nombre)."),
                text("tag", "Píldora debajo del título."), tone("tag_tone", "Color de la píldora."),
                text("trailing", "Valor a la derecha (un total, una cifra)."), text("trailing_caption", "Línea chica debajo del valor (una hora)."),
                enum("trailing_tone", "Color del valor y su línea chica.", TEXT_TONES),
                bool("chevron", "Flecha a la derecha (lleva a otra pantalla)."),
                enum("overline_tone", "Color de la línea de arriba.", TEXT_TONES),
                bool("emphasis", "Título más marcado (algo sin leer)."),
                text("badge", "Número en una píldora a la derecha (no leídos); vacío o 0 no se muestra."),
                text("badge_label", "Lo que dice TalkBack del número («2 mensajes sin leer»)."),
                text("caption", "Tercera línea chica (quién atiende, en qué va)."), icon("caption_icon", "Ícono chico antes de la tercera línea."),
                enum("caption_icon_tone", "Color de ese ícono.", TEXT_TONES)),
            tappable = true,
        ),
        ComponentSpec(
            "stat", "Una cifra con su nombre (para tableros).",
            listOf(text("label", "Qué es («Ventas de hoy»).", required = true), text("value", "La cifra.", required = true),
                text("caption", "Línea chica debajo."), tone(), icon("icon", "Ícono arriba.")),
            tappable = true,
        ),
        ComponentSpec(
            "key_value", "Un dato con su etiqueta, uno debajo del otro (como en la ficha del pedido).",
            listOf(text("label", "La etiqueta.", required = true), text("value", "El dato.", required = true)),
        ),
        ComponentSpec(
            "stepper", "Los pasos de un proceso con el actual marcado (las etapas de un pedido).",
            listOf(PropSpec("steps", PropKind.OPTIONS, "Los pasos en orden: `[{ \"value\": \"…\", \"label\": \"…\" }]`.", required = true),
                text("current", "El valor del paso actual.", required = true), text("label", "Lo que dice TalkBack («Etapa: Preparando»).")),
        ),
        ComponentSpec(
            "progress", "Barra de avance. Sin `value` gira indefinida.",
            listOf(text("value", "Avance de 0 a 1."), text("label", "Texto encima."), tone()),
        ),
        ComponentSpec(
            "notice", "Aviso en una franja de color. Se puede tocar.",
            listOf(text("text", "El aviso.", required = true), text("title", "Título."),
                enum("tone", "Color (por defecto info).", listOf("info", "success", "warning", "danger")), icon("icon", "Ícono.")),
            tappable = true,
        ),
        ComponentSpec(
            "empty", "Estado vacío: ícono grande, título y ayuda.",
            listOf(text("title", "Título.", required = true), text("body", "Línea de ayuda."), icon("icon", "Ícono.")),
        ),
        // ── Acciones y entradas ──────────────────────────────────────────────────────────────────────
        ComponentSpec(
            "button", "Un botón. Necesita `action`.",
            listOf(text("text", "El texto.", required = true),
                enum("style", "Relleno (por defecto), tonal, con borde o solo texto.", listOf("filled", "tonal", "outlined", "text")),
                icon("icon", "Ícono a la izquierda."), bool("enabled", "Si se puede tocar (`{{form.guia | present}}`)."),
                bool("full_width", "Ocupa todo el ancho."), enum("tone", "`danger` para acciones que borran o cancelan.", listOf("default", "danger"))),
            tappable = true, actionRequired = true,
        ),
        ComponentSpec(
            "chips", "Elige UNA opción. Guarda el valor en `bind` (`state.x` vuelve a pedir los datos que lo usan).",
            listOf(PropSpec("bind", PropKind.BIND, "Dónde se guarda: `state.<clave>`.", required = true),
                PropSpec("options", PropKind.OPTIONS, "`[{ \"value\": \"…\", \"label\": \"…\" }]`.", required = true),
                enum("style", "Chips (por defecto) o botones segmentados.", listOf("chips", "segmented"))),
        ),
        ComponentSpec(
            "text_field", "Campo de texto. Lo escrito queda en `bind` (`form.x` para mandarlo en una llamada).",
            listOf(PropSpec("bind", PropKind.BIND, "Dónde se guarda: `form.<clave>` o `state.<clave>`.", required = true),
                text("label", "Etiqueta.", required = true), text("placeholder", "Ejemplo dentro del campo."),
                enum("keyboard", "Teclado.", listOf("text", "number", "phone", "email", "url")), bool("multiline", "Varias líneas."),
                text("value", "Valor inicial."), text("max_length", "Como mucho N letras (un número o un dato: `{{v.max_length}}`).")),
        ),
        ComponentSpec(
            "switch", "Interruptor sí/no. Guarda true/false en `bind`.",
            listOf(PropSpec("bind", PropKind.BIND, "Dónde se guarda.", required = true), text("label", "Etiqueta.", required = true),
                text("description", "Línea de ayuda.")),
        ),
        // ── Piezas nativas (lo crítico del teléfono: el chat, permisos) ──────────────────────────────
        ComponentSpec(
            "chat", "El chat nativo de una conversación: lo que entendió el bot, el historial, deshacer, las burbujas y el composer. Llena el espacio (pantalla con `\"layout\": \"fill\"` y `\"weight\": 1`).",
            listOf(text("session", "La conversación (`wa_…`).", required = true),
                PropSpec("on_more", PropKind.ACTION, "Lo que hace «+ Más» y mantener una burbuja (abrir la paleta de acciones)."),
                PropSpec("on_reactivate", PropKind.ACTION, "Lo que hace «Reactivar con plantilla» con la ventana de 24 h cerrada.")),
            native = true,
        ),
        ComponentSpec("notifications_banner", "Aviso para activar las notificaciones (solo si están apagadas; pide el permiso).", emptyList(), native = true),
    ).associateBy { it.type }

    /**
     * Fuentes de datos del TELÉFONO (`"app": "<nombre>"`): lo que ya vive en la app y se mantiene al día solo (Room con el
     * SSE, lo no leído de este teléfono). Cada una dice qué campos trae.
     */
    val appSources: Map<String, String> = linkedMapOf(
        "conversations" to "Los chats de la bandeja: session_id, title (nombre o número), phone, name, detail («Bot · Interesado · Pedido #41»), preview, unseen, unseen_label, unread, human, has_order, all, time_ms.",
        "fires" to "Los incendios (graves primero): fire_id, kind (chat/order), severity (grave/hoy/espera), overline («CHAT · GRAVE»), title, subtitle, getting_worse, session_id, order_id, opens (chat/order), all, grave, chat, order.",
        "radar" to "Los incendios graves que el operador no ocultó (lo que cuenta el chip del radar). Mismos campos que fires.",
        "chat" to "Una conversación (params: session): session_id, title, subtitle («Tú atiendes»), name, phone, human, window_open, order_id, order_label («Pedido #41»), stage.",
        "templates" to "Las plantillas aprobadas: name, title, label (con «· recomendada»), body, preview_body ({{variable}}), fallback, needs_image, is_default, variables [{name, label, max_length}], variable_names.",
        "app" to "La app: privacy (si hay política de privacidad para abrir).",
    )

    /** Parámetros obligatorios de cada fuente del teléfono. */
    val appSourceParams: Map<String, List<String>> = mapOf("chat" to listOf("session"))

    /** Acciones que hace la app con lo suyo (`{"type": "native", "name": "…", "args": {…}}`). */
    val nativeActions: Map<String, String> = linkedMapOf(
        "sign_out" to "Cierra la sesión y borra del teléfono los chats, borradores y avisos.",
        "open_privacy" to "Abre la política de privacidad.",
        "hide_fire" to "Saca un incendio del radar (sigue en Incendios). args: fire_id.",
        "take_over" to "El operador toma la conversación. args: session.",
        "return_to_bot" to "Devuelve la conversación al bot. args: session.",
        "send_tool" to "Manda una acción del bot por el outbox, con deshacer. args: session, tool, label.",
        "send_template" to "Manda una plantilla aprobada por el outbox. args: session, template, values.",
    )

    /** Argumentos obligatorios de cada acción nativa. */
    val nativeActionArgs: Map<String, List<String>> = mapOf(
        "hide_fire" to listOf("fire_id"),
        "take_over" to listOf("session"),
        "return_to_bot" to listOf("session"),
        "send_tool" to listOf("session", "tool", "label"),
        "send_template" to listOf("session", "template", "values"),
    )

    /** Cómo se arma el cuerpo de una pantalla. */
    val layouts = setOf("list", "fill")

    /** Estilos de las acciones de la barra superior. */
    val topActionStyles = setOf("icon", "chip", "menu")

    /** Íconos de Material Symbols Rounded que trae la app (`OperatorIcons.named`). */
    val icons: Set<String> = linkedSetOf(
        "add", "apps", "arrow_back", "bar_chart", "bolt", "bot", "calendar", "call", "campaign", "chat", "chat_filled", "check",
        "check_circle", "chevron_right", "close", "edit", "error", "filter", "fire", "fire_filled", "group", "info", "inventory",
        "link", "location", "mail", "more_vert", "notifications", "orders", "orders_filled", "payments", "person", "receipt",
        "refresh", "schedule", "search", "sell", "send", "settings", "shipping", "star", "storefront",
        "trending_down", "trending_up", "undo", "warning",
    )

    /** Acciones que la app sabe ejecutar. */
    val actions: Map<String, String> = linkedMapOf(
        "navigate" to "Abre otra pantalla del servidor: `screen`, `params` y `sheet: true` para abrirla como hoja.",
        "open_chat" to "Abre el chat de una conversación: `session` (`live: true` lo abre en la pila de Incendios).",
        "open_order" to "Abre la ficha nativa de un pedido: `order`.",
        "open_url" to "Abre un enlace https (o `tel:`): `url`.",
        "back" to "Vuelve a la pantalla anterior.",
        "refresh" to "Vuelve a pedir los datos (todos, o los de `data: [ids]`).",
        "set_state" to "Cambia valores de `state` (`values`); los datos que los usan se vuelven a pedir.",
        "call" to "Llama al backend: `method` (POST, PUT, PATCH, DELETE), `path`, `query`, `body`, `confirm`, `success` y `then`.",
        "message" to "Muestra un aviso corto abajo: `text`.",
        "copy" to "Copia un texto: `text`.",
        "native" to "Algo que hace la app con lo suyo: `name` (ver acciones nativas) y `args`.",
        "confirm" to "Pide confirmación (`title`, `body`, `accept`, `dismiss`) y, si el operador acepta, hace `then`.",
        "if" to "Si `condition` da verdadero hace `then`; si no, `else`.",
    )

    /** Nombres que no puede tener una fuente de datos: ya significan algo en las plantillas. */
    val reservedNames = setOf("params", "state", "form", "item", "index", "now", "screen", "status")

    /** Claves comunes a todos los nodos (no son propiedades del componente). */
    val nodeKeys = setOf("type", "id", "visible", "action", "weight", "children", "item", "empty", "as")

    /** Eventos del servidor a los que una fuente puede atarse con `refreshOn`. */
    val eventDomains = setOf("chats", "orders", "marketing", "fires")
}
