package com.hubara.operator.core.sdui

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull

/**
 * Una pantalla definida por el servidor (un archivo de `android_operator/screens/`).
 *
 * - [data]: de qué endpoints salen los datos. Cada fuente se lee en las plantillas por su id (`{{pedidos.orders}}`).
 * - [state]: valores que la pantalla cambia sola (un filtro). Si una fuente los usa, se vuelve a pedir al cambiar.
 * - [params]: lo que recibe al abrirse (`{{params.campaign_id}}`); los de la lista son obligatorios.
 * - [computed]: valores calculados con nombre, para no repetir la misma cadena de filtros (`{{validos | count}}`). Se
 *   evalúan en orden: cada uno puede usar los anteriores, los datos, `state`, `params` y `form`.
 * - [body]: el árbol de componentes del [Catalog].
 */
data class ScreenDoc(
    val id: String,
    val title: Template?,
    val requires: Int,
    val params: List<String>,
    val state: Map<String, JsonElement>,
    val data: Map<String, DataSource>,
    val computed: Map<String, Template> = emptyMap(),
    val body: List<Node>,
    val topActions: List<TopAction>,
    val fab: Fab?,
    /** `list` (por defecto): el cuerpo se desliza. `fill`: el cuerpo llena la pantalla (el chat). */
    val layout: String = "list",
    /** Segunda línea del encabezado. */
    val subtitle: Template? = null,
    /** Nombre para un avatar a la izquierda del título (y su semilla de color). */
    val avatar: Template? = null,
    val avatarSeed: Template? = null,
)

/** Una fuente de datos: un GET a NUESTRO backend (ruta relativa `/api/...`). */
data class DataSource(
    val id: String,
    val method: String,
    val path: Template,
    val query: Map<String, Template>,
    /** Si falla, la pantalla igual se muestra (lo que la usa queda vacío). */
    val optional: Boolean,
    /** Eventos del servidor que la vuelven a pedir (`orders`, `chats`, `marketing`). */
    val refreshOn: Set<String>,
    /** Cada cuántos segundos se vuelve a pedir mientras la pantalla está a la vista (mínimo 15). */
    val every: Int?,
    val location: String,
    /** Una fuente del TELÉFONO (la bandeja en Room, lo no leído…) en vez de un GET: su nombre y sus parámetros. */
    val app: String? = null,
    val appParams: Map<String, Template> = emptyMap(),
)

/** Lo que se le pide a una fuente del teléfono: su nombre y los parámetros ya evaluados. */
data class AppRequest(val name: String, val params: Map<String, String>)

/**
 * Una acción de la barra superior. [style]: `icon` (un ícono), `chip` (un botón con texto, p. ej. «Pedido #41») o
 * `menu` (un renglón del menú «Más opciones»). [visible]: se muestra solo si da verdadero.
 */
data class TopAction(
    val icon: String,
    val label: Template,
    val action: Action?,
    val style: String = "icon",
    val visible: List<Template> = emptyList(),
)

data class Fab(val label: String, val icon: String?, val action: Action?)

/**
 * Un componente del árbol. Las propiedades se escriben al mismo nivel que `type` (`{"type": "text", "text": "Hola"}`);
 * las que son texto ya vienen leídas como [Template]. [location] dice dónde está en el archivo (`body[1].children[0]`).
 */
class Node internal constructor(
    val type: String,
    val props: Map<String, JsonElement>,
    val children: List<Node>,
    val item: Node?,
    val empty: Node?,
    val alias: String,
    val visible: List<Template>,
    val action: Action?,
    val id: String?,
    val weight: Float?,
    val location: String,
) {
    private val templates: Map<String, Template> = props.mapNotNull { (key, value) ->
        (value as? JsonPrimitive)?.takeIf { it.isString }?.let { key to Template.parseOrLiteral(it.content) }
    }.toMap()

    /** La propiedad de texto como plantilla (null si no está o no es texto). */
    fun template(key: String): Template? = templates[key]

    /** El texto tal cual, sin evaluar (para `bind`, `style`…). */
    fun literal(key: String): String? = (props[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

    fun number(key: String): Double? = (props[key] as? JsonPrimitive)?.content?.toDoubleOrNull()

    /** Un valor de verdadero/falso: un `true` literal o una plantilla que se evalúa. */
    fun flag(key: String, scope: Scope, env: Env, default: Boolean = false): Boolean {
        val raw = props[key] ?: return default
        (raw as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull?.let { return it }
        return templates[key]?.evaluate(scope, env).truthy
    }

    /** Si se pinta: todas las condiciones de `visible` tienen que dar verdadero. */
    fun isVisible(scope: Scope, env: Env): Boolean = visible.all { it.evaluate(scope, env).truthy }

    /** Los elementos de una `list`: la lista literal de `items` o lo que dé su plantilla. */
    fun items(scope: Scope, env: Env): List<JsonElement> =
        (props["items"] as? JsonArray) ?: (templates["items"]?.evaluate(scope, env) as? JsonArray) ?: emptyList()

    private val actionProps: Map<String, Action> by lazy {
        props.mapNotNull { (key, value) ->
            if (value is JsonObject && value["type"] != null) parseAction(value, "$location.$key")?.let { key to it } else null
        }.toMap()
    }

    /** Una propiedad que es una acción (`"on_more": {"type": "navigate", …}`), para los componentes nativos. */
    fun actionProp(key: String): Action? = actionProps[key]

    override fun toString() = "Node($type @ $location)"
}

/** Lo que hace un toque. Es un conjunto cerrado: el JSON no puede ejecutar código ni saltarse una confirmación. */
sealed interface Action {
    data class Navigate(val screen: String, val params: Map<String, Template>, val sheet: Boolean) : Action
    data class OpenChat(val session: Template, val live: Boolean = false) : Action
    data class OpenOrder(val order: Template) : Action
    data class OpenUrl(val url: Template) : Action
    data object Back : Action
    data class Refresh(val sources: List<String>) : Action
    data class SetState(val values: Map<String, JsonElement>) : Action
    data class Call(
        val method: String,
        val path: Template,
        val query: Map<String, Template>,
        val body: JsonElement?,
        val confirm: Confirm?,
        val success: Template?,
        val then: List<Action>,
    ) : Action
    data class Message(val text: Template) : Action
    data class Copy(val text: Template) : Action
    data class Sequence(val actions: List<Action>) : Action

    /** Una acción que hace la app con lo suyo (outbox, sesión, incendios). Los nombres están en [Catalog.nativeActions]. */
    data class Native(val name: String, val args: JsonElement?) : Action

    /** Pide confirmación y, si el operador acepta, hace [then]. */
    data class AskFirst(val confirm: Confirm, val then: Action?) : Action

    /** Si [condition] da verdadero hace [then]; si no, [otherwise]. */
    data class If(val condition: Template, val then: Action?, val otherwise: Action?) : Action
    data class Unknown(val type: String, val location: String) : Action
}

data class Confirm(val title: Template, val body: Template?, val accept: String, val dismiss: String)

data class ParsedScreen(val doc: ScreenDoc?, val problems: List<String>)

/** Las pestañas extra de la app (además de Chats, Incendios y Órdenes), cada una una pantalla del servidor. */
data class AppManifest(val tabs: List<TabSpec>)

data class TabSpec(
    val screen: String,
    val label: String,
    val icon: String,
    /** El ícono cuando la pestaña está elegida (relleno). */
    val iconSelected: String? = null,
    /** Un número sobre el ícono, de las fuentes del teléfono (`{{radar | count}}`); 0 o vacío no se muestra. */
    val badge: Template? = null,
)

data class ParsedManifest(val manifest: AppManifest?, val problems: List<String>)

private val LENIENT = Json { isLenient = false }

/** Lee una pantalla. Sin JSON, sin `id` o sin `body` no hay pantalla; lo demás se lee aunque tenga errores (los reporta [validateScreen]). */
fun parseScreen(raw: String): ParsedScreen {
    val problems = mutableListOf<String>()
    val root = runCatching { LENIENT.parseToJsonElement(raw) }.getOrNull() as? JsonObject
        ?: return ParsedScreen(null, listOf("No es un JSON de pantalla (¿un 404 o HTML?)."))
    val id = root.str("id")?.takeIf { it.isNotBlank() }
    if (id == null) problems += "Falta «id»."
    val body = root["body"] as? JsonArray
    if (body == null) problems += "Falta «body» (la lista de componentes)."
    if (id == null || body == null) return ParsedScreen(null, problems)

    val doc = ScreenDoc(
        id = id,
        title = root.str("title")?.let(Template::parseOrLiteral),
        requires = (root["requires"] as? JsonPrimitive)?.intOrNull ?: 1,
        params = (root["params"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.content },
        state = (root["state"] as? JsonObject).orEmpty().filterKeys { !it.isNote() },
        data = (root["data"] as? JsonObject).orEmpty().filterKeys { !it.isNote() }.mapValues { (key, value) ->
            parseSource(key, value, problems)
        },
        computed = root.templates("computed").filterKeys { !it.isNote() },
        body = body.mapIndexedNotNull { i, el -> parseNode(el, "body[$i]", problems) },
        topActions = (root["topActions"] as? JsonArray).orEmpty().mapIndexedNotNull { i, el ->
            val o = el as? JsonObject ?: return@mapIndexedNotNull null
            TopAction(
                icon = o.str("icon").orEmpty(),
                label = Template.parseOrLiteral(o.str("label").orEmpty()),
                action = parseAction(o["action"], "topActions[$i].action"),
                style = o.str("style") ?: "icon",
                visible = visibleOf(o["visible"]),
            )
        },
        fab = (root["fab"] as? JsonObject)?.let { o -> Fab(o.str("label").orEmpty(), o.str("icon"), parseAction(o["action"], "fab.action")) },
        layout = root.str("layout") ?: "list",
        subtitle = root.str("subtitle")?.let(Template::parseOrLiteral),
        avatar = root.str("avatar")?.let(Template::parseOrLiteral),
        avatarSeed = root.str("avatar_seed")?.let(Template::parseOrLiteral),
    )
    return ParsedScreen(doc, problems)
}

fun parseAppManifest(raw: String): ParsedManifest {
    val root = runCatching { LENIENT.parseToJsonElement(raw) }.getOrNull() as? JsonObject
        ?: return ParsedManifest(null, listOf("No es un JSON de manifiesto."))
    val tabs = (root["tabs"] as? JsonArray).orEmpty().mapNotNull { el ->
        val o = el as? JsonObject ?: return@mapNotNull null
        TabSpec(
            screen = o.str("screen").orEmpty(),
            label = o.str("label").orEmpty(),
            icon = o.str("icon").orEmpty(),
            iconSelected = o.str("icon_selected"),
            badge = o.str("badge")?.let(Template::parseOrLiteral),
        )
    }
    return ParsedManifest(AppManifest(tabs), emptyList())
}

private fun visibleOf(el: JsonElement?): List<Template> = when (el) {
    is JsonArray -> el.mapNotNull { (it as? JsonPrimitive)?.let { p -> Template.parseOrLiteral(p.content) } }
    is JsonPrimitive -> if (el is JsonNull) emptyList() else listOf(Template.parseOrLiteral(el.content))
    else -> emptyList()
}

private fun parseSource(id: String, el: JsonElement, problems: MutableList<String>): DataSource {
    val o = el as? JsonObject ?: JsonObject(emptyMap())
    val path = o.str("get")
    val app = o.str("app")
    when {
        path != null && app != null -> problems += "data.$id: usa «get» o «app», no los dos."
        path == null && app == null -> problems += "data.$id: falta «get» con la ruta del endpoint (`/api/...`) o «app» con una fuente del teléfono."
    }
    return DataSource(
        id = id,
        method = if (app != null && path == null) "APP" else "GET",
        path = Template.parseOrLiteral(path.orEmpty()),
        query = o.templates("query"),
        optional = (o["optional"] as? JsonPrimitive)?.booleanOrNull == true,
        refreshOn = (o["refreshOn"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.content }.toSet(),
        every = (o["every"] as? JsonPrimitive)?.intOrNull,
        location = "data.$id",
        app = app,
        appParams = o.templates("params"),
    )
}

private fun parseNode(el: JsonElement?, location: String, problems: MutableList<String>): Node? {
    val o = el as? JsonObject
    if (o == null) {
        problems += "$location: cada componente es un objeto `{\"type\": …}`."
        return null
    }
    val type = o.str("type")
    if (type.isNullOrBlank()) {
        problems += "$location: falta «type»."
        return null
    }
    val visible = visibleOf(o["visible"])
    return Node(
        type = type,
        props = o.filterKeys { it !in Catalog.nodeKeys && !it.isNote() },
        children = (o["children"] as? JsonArray).orEmpty().mapIndexedNotNull { i, c -> parseNode(c, "$location.children[$i]", problems) },
        item = o["item"]?.let { parseNode(it, "$location.item", problems) },
        empty = o["empty"]?.let { parseNode(it, "$location.empty", problems) },
        alias = o.str("as")?.takeIf { it.isNotBlank() } ?: "item",
        visible = visible,
        action = parseAction(o["action"], "$location.action"),
        id = o.str("id"),
        weight = (o["weight"] as? JsonPrimitive)?.content?.toFloatOrNull(),
        location = location,
    )
}

internal fun parseAction(el: JsonElement?, location: String): Action? = when (el) {
    null, JsonNull -> null
    is JsonArray -> Action.Sequence(el.mapIndexedNotNull { i, a -> parseAction(a, "$location[$i]") })
    is JsonObject -> when (val type = el.str("type").orEmpty()) {
        "navigate" -> Action.Navigate(el.str("screen").orEmpty(), el.templates("params"), (el["sheet"] as? JsonPrimitive)?.booleanOrNull == true)
        "open_chat" -> Action.OpenChat(Template.parseOrLiteral(el.str("session").orEmpty()), (el["live"] as? JsonPrimitive)?.booleanOrNull == true)
        "open_order" -> Action.OpenOrder(Template.parseOrLiteral(el.str("order").orEmpty()))
        "open_url" -> Action.OpenUrl(Template.parseOrLiteral(el.str("url").orEmpty()))
        "back" -> Action.Back
        "refresh" -> Action.Refresh((el["data"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.content })
        "set_state" -> Action.SetState((el["values"] as? JsonObject).orEmpty())
        "call" -> Action.Call(
            method = el.str("method").orEmpty().uppercase(),
            path = Template.parseOrLiteral(el.str("path").orEmpty()),
            query = el.templates("query"),
            body = el["body"],
            confirm = (el["confirm"] as? JsonObject)?.let(::confirmOf),
            success = el.str("success")?.let(Template::parseOrLiteral),
            then = when (val then = el["then"]) {
                is JsonArray -> then.mapIndexedNotNull { i, a -> parseAction(a, "$location.then[$i]") }
                null -> emptyList()
                else -> listOfNotNull(parseAction(then, "$location.then"))
            },
        )
        "message" -> Action.Message(Template.parseOrLiteral(el.str("text").orEmpty()))
        "copy" -> Action.Copy(Template.parseOrLiteral(el.str("text").orEmpty()))
        "native" -> Action.Native(el.str("name").orEmpty(), el["args"])
        "confirm" -> Action.AskFirst(confirmOf(el), parseAction(el["then"], "$location.then"))
        "if" -> Action.If(
            condition = Template.parseOrLiteral(el.str("condition").orEmpty()),
            then = parseAction(el["then"], "$location.then"),
            otherwise = parseAction(el["else"], "$location.else"),
        )
        else -> Action.Unknown(type, location)
    }
    else -> Action.Unknown(el.toString(), location)
}

private fun confirmOf(c: JsonObject) = Confirm(
    title = Template.parseOrLiteral(c.str("title") ?: "¿Seguro?"),
    body = c.str("body")?.let(Template::parseOrLiteral),
    accept = c.str("accept") ?: "Sí",
    dismiss = c.str("dismiss") ?: "Cancelar",
)

private fun String.isNote() = startsWith("_") || startsWith("$")

private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content

private fun JsonObject.templates(key: String): Map<String, Template> =
    (this[key] as? JsonObject).orEmpty().mapNotNull { (k, v) ->
        (v as? JsonPrimitive)?.takeIf { it !is JsonNull }?.let { k to Template.parseOrLiteral(it.content) }
    }.toMap()

private fun JsonObject?.orEmpty(): JsonObject = this ?: JsonObject(emptyMap())

private fun JsonArray?.orEmpty(): List<JsonElement> = this ?: emptyList()
