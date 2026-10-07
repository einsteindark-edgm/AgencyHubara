package com.hubara.operator.core.sdui

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * Revisa que ESTA versión de la app pueda pintar la pantalla entera. La corre la CI sobre cada archivo de
 * `android_operator/screens/` ([knownScreens]: id → parámetros obligatorios de cada pantalla del repo). Cada error
 * dice dónde está y qué arreglar.
 */
fun validateScreen(doc: ScreenDoc, knownScreens: Map<String, List<String>>): List<String> =
    ScreenValidator(doc, knownScreens).run()

fun validateAppManifest(manifest: AppManifest, knownScreens: Map<String, List<String>>): List<String> = buildList {
    if (manifest.tabs.size !in MIN_TABS..MAX_TABS) {
        add("La barra de abajo lleva entre $MIN_TABS y $MAX_TABS pestañas (hay ${manifest.tabs.size}).")
    }
    manifest.tabs.forEachIndexed { i, tab ->
        if (tab.screen !in knownScreens) add("tabs[$i]: la pantalla «${tab.screen}» no existe en android_operator/screens/.")
        else if (knownScreens.getValue(tab.screen).isNotEmpty()) add("tabs[$i]: «${tab.screen}» necesita parámetros; una pestaña no puede pasárselos.")
        if (tab.label.isBlank()) add("tabs[$i]: falta «label».")
        listOfNotNull(tab.icon, tab.iconSelected).filter { it !in Catalog.icons }.forEach {
            add("tabs[$i]: el ícono «$it» no existe (los que hay: ${Catalog.icons.joinToString()}).")
        }
        tab.badge?.let { badge ->
            runCatching { Template.parse(badge.raw) }.onFailure { add("tabs[$i].badge: ${it.message}") }.getOrNull()
                ?.roots()?.filter { it !in Catalog.appSources }?.forEach {
                    add("tabs[$i].badge: «$it» no es una fuente del teléfono (las que hay: ${Catalog.appSources.keys.joinToString()}).")
                }
        }
    }
    val duplicated = manifest.tabs.groupBy { it.screen }.filterValues { it.size > 1 }.keys
    duplicated.forEach { add("La pantalla «$it» está en dos pestañas.") }
}

/** Pestañas de la barra de abajo (Material: 3 a 5 destinos; 2 se admite para un menú mínimo). */
const val MIN_TABS = 2
const val MAX_TABS = 5

private val CALL_METHODS = setOf("GET", "POST", "PUT", "PATCH", "DELETE")

/** Una ruta a NUESTRO backend: relativa, bajo `/api/`, sin `..`. Lo mismo se revisa al llamar ([isSafeApiPath]). */
private fun pathProblem(path: Template): String? {
    val prefix = path.literalPrefix()
    val ok = prefix.startsWith("/api/") && !prefix.startsWith("//") && "://" !in path.raw &&
        path.raw.split('?').first().split('/').none { it == ".." || it == "." }
    return if (ok) null else "«${path.raw}» tiene que ser una ruta de nuestro backend que empiece con /api/ (sin dominio ni ..)."
}

private class ScreenValidator(private val doc: ScreenDoc, private val known: Map<String, List<String>>) {
    private val problems = mutableListOf<String>()
    private val stateKeys = doc.state.keys

    fun run(): List<String> {
        if (doc.requires > Catalog.VERSION) {
            problems += "«requires»: ${doc.requires} pide un catálogo más nuevo que el de esta app (${Catalog.VERSION}). " +
                "Si agregaste un componente, súbelo en Catalog.VERSION."
        }
        if (doc.layout !in Catalog.layouts) problems += "«layout»: «${doc.layout}» no vale; usa ${Catalog.layouts.joinToString()}."
        val base = doc.data.keys + setOf("params", "state", "form", "now", "status")
        doc.data.values.forEach(::source)
        // Los calculados, en orden: cada uno ve los anteriores.
        val names = doc.computed.entries.fold(base) { visible, (name, t) ->
            when {
                name in Catalog.reservedNames -> problems += "computed.$name: «$name» es un nombre reservado (${Catalog.reservedNames.joinToString()}); usa otro."
                name in doc.data -> problems += "computed.$name: ya hay una fuente de datos «$name»; usa otro nombre."
            }
            template(t.raw, "computed.$name", visible)
            visible + name
        }
        doc.title?.let { template(it.raw, "title", names) }
        doc.subtitle?.let { template(it.raw, "subtitle", names) }
        doc.avatar?.let { template(it.raw, "avatar", names) }
        doc.avatarSeed?.let { template(it.raw, "avatar_seed", names) }
        doc.topActions.forEachIndexed { i, top ->
            icon(top.icon, "topActions[$i]")
            if (top.label.raw.isBlank()) problems += "topActions[$i]: falta «label» (lo lee TalkBack)."
            template(top.label.raw, "topActions[$i].label", names)
            if (top.style !in Catalog.topActionStyles) {
                problems += "topActions[$i].style: «${top.style}» no vale; usa ${Catalog.topActionStyles.joinToString()}."
            }
            top.visible.forEach { template(it.raw, "topActions[$i].visible", names) }
            action(top.action, "topActions[$i].action", names)
        }
        doc.fab?.let { fab ->
            fab.icon?.let { icon(it, "fab") }
            if (fab.label.isBlank()) problems += "fab: falta «label»."
            if (fab.action == null) problems += "fab: falta «action»."
            action(fab.action, "fab.action", names)
        }
        doc.body.forEach { node(it, names) }
        return problems
    }

    private fun source(s: DataSource) {
        if (s.id in Catalog.reservedNames) problems += "${s.location}: «${s.id}» es un nombre reservado (${Catalog.reservedNames.joinToString()}); usa otro."
        val names = setOf("params", "state", "now")
        if (s.app != null) {
            if (s.app !in Catalog.appSources) {
                problems += "${s.location}.app: no hay una fuente del teléfono «${s.app}» (las que hay: ${Catalog.appSources.keys.joinToString()})."
            }
            Catalog.appSourceParams[s.app].orEmpty().filter { it !in s.appParams }.forEach {
                problems += "${s.location}.params: la fuente «${s.app}» necesita «$it»."
            }
            s.appParams.forEach { (k, t) -> template(t.raw, "${s.location}.params.$k", names) }
            return
        }
        if (s.path.raw.isEmpty()) return
        pathProblem(s.path)?.let { problems += "${s.location}.get: $it" }
        template(s.path.raw, "${s.location}.get", names)
        s.query.forEach { (k, t) -> template(t.raw, "${s.location}.query.$k", names) }
        s.refreshOn.filter { it !in Catalog.eventDomains }.forEach {
            problems += "${s.location}.refreshOn: no hay eventos «$it» (los que hay: ${Catalog.eventDomains.joinToString()})."
        }
        s.every?.let { if (it < 15) problems += "${s.location}.every: mínimo 15 segundos (hay $it)." }
    }

    private fun node(n: Node, names: Set<String>) {
        val spec = Catalog.components[n.type]
        if (spec == null) {
            problems += "${n.location}: componente desconocido «${n.type}» (los que hay: ${Catalog.components.keys.joinToString()})."
            return
        }
        if (spec.since > doc.requires) {
            problems += "${n.location}: «${n.type}» es del catálogo ${spec.since}; pon \"requires\": ${spec.since} en la pantalla."
        }
        val itemNames = names + n.alias + "index"
        n.props.forEach { (key, value) ->
            val prop = spec.prop(key)
            if (prop == null) {
                problems += "${n.location}: «$key» no es una propiedad de «${n.type}» (las que tiene: ${spec.props.joinToString { it.name }.ifEmpty { "ninguna" }})."
            } else {
                prop(n, prop, value, if (spec.list && key == "key") itemNames else names)
            }
        }
        spec.props.filter { it.required && it.name !in n.props }.forEach {
            problems += "${n.location}: falta «${it.name}» en «${n.type}» (${it.doc})"
        }
        n.visible.forEach { template(it.raw, "${n.location}.visible", names) }
        if (n.children.isNotEmpty() && !spec.container) problems += "${n.location}: «${n.type}» no lleva «children»."
        if (spec.list) {
            val repeats = "items" in n.props || n.item != null
            when {
                n.children.isNotEmpty() && repeats ->
                    problems += "${n.location}: una «list» tiene renglones fijos («children») o repetidos («items» + «item»), no las dos."
                n.children.isEmpty() && ("items" !in n.props || n.item == null) ->
                    problems += "${n.location}: falta «items» + «item» (lo que se repite) o «children» (renglones fijos)."
            }
            n.item?.let { node(it, itemNames) }
            n.empty?.let { node(it, names) }
        } else if (n.item != null || n.empty != null) {
            problems += "${n.location}: «item» y «empty» son solo de «list»."
        }
        when {
            n.action != null && !spec.tappable -> problems += "${n.location}: «${n.type}» no se puede tocar; quita «action»."
            n.action == null && spec.actionRequired -> problems += "${n.location}: falta la acción («action») de «${n.type}»."
        }
        action(n.action, "${n.location}.action", names)
        n.children.forEach { node(it, names) }
    }

    private fun prop(n: Node, prop: PropSpec, value: JsonElement, names: Set<String>) {
        val where = "${n.location}.${prop.name}"
        val primitive = value as? JsonPrimitive
        val str = primitive?.takeIf { it.isString }?.content
        when (prop.kind) {
            PropKind.TEXT -> if (primitive == null || primitive is JsonNull) problems += "$where: tiene que ser un texto." else str?.let { template(it, where, names) }
            PropKind.BOOL -> when {
                primitive?.booleanOrNull != null && !primitive.isString -> Unit
                str != null -> template(str, where, names)
                else -> problems += "$where: tiene que ser true/false o una plantilla."
            }
            PropKind.NUMBER -> if (primitive == null || primitive.isString || primitive.content.toDoubleOrNull() == null) {
                problems += "$where: tiene que ser un número."
            }
            PropKind.ENUM -> when {
                str == null -> problems += "$where: tiene que ser uno de ${prop.values.joinToString()}."
                str.contains("{{") -> template(str, where, names)
                str !in prop.values -> problems += "$where: «$str» no vale; usa uno de ${prop.values.joinToString()}."
            }
            PropKind.ICON -> when {
                str == null -> problems += "$where: tiene que ser el nombre de un ícono."
                str.contains("{{") -> template(str, where, names)
                else -> icon(str, where)
            }
            PropKind.LIST -> when {
                value is JsonArray -> Unit
                str == null -> problems += "$where: tiene que ser una expresión (`{{fuente.campo}}`) o una lista `[…]`."
                else -> template(str, where, names)?.let { t ->
                    if (!t.isExpressionOnly) problems += "$where: tiene que ser UNA expresión que dé una lista (`{{fuente.campo}}`)."
                }
            }
            PropKind.OPTIONS -> when (value) {
                is JsonArray -> value.forEachIndexed { i, el ->
                    val o = el as? JsonObject
                    if (o == null || o["value"] == null || o["label"] == null) problems += "$where[$i]: cada opción es {\"value\": …, \"label\": …}."
                    else (o["label"] as? JsonPrimitive)?.content?.let { template(it, "$where[$i].label", names) }
                }
                else -> str?.let { template(it, where, names) } ?: run { problems += "$where: tiene que ser una lista de opciones." }
            }
            PropKind.BIND -> bind(str, where, names)
            PropKind.ACTION -> if (value !is JsonObject) problems += "$where: tiene que ser una acción `{\"type\": …}`." else action(n.actionProp(prop.name), where, names)
        }
    }

    private fun bind(raw: String?, where: String, names: Set<String>) {
        // La clave puede salir de un dato (`form.{{v.name}}`, un campo por variable de una plantilla): solo se revisa
        // que vaya a `form` o a `state` y que la plantilla se lea.
        if (raw != null && "{{" in raw) {
            val where2 = raw.substringBefore('.')
            if (where2 !in setOf("state", "form") || !raw.startsWith("$where2.")) {
                problems += "$where: usa form.<clave> (para mandarlo en una llamada) o state.<clave> (para filtrar); hay «$raw»."
            }
            template(raw.substringAfter('.'), where, names)
            return
        }
        val parts = raw?.split('.')
        when {
            parts == null || parts.size != 2 || parts[0] !in setOf("state", "form") || parts[1].isBlank() ->
                problems += "$where: usa form.<clave> (para mandarlo en una llamada) o state.<clave> (para filtrar); hay «$raw»."
            parts[0] == "state" && parts[1] !in stateKeys ->
                problems += "$where: «${parts[1]}» no está en «state» (decláralo con su valor inicial: \"state\": {\"${parts[1]}\": …})."
        }
    }

    private fun action(a: Action?, where: String, names: Set<String>) {
        when (a) {
            null -> Unit
            is Action.Navigate -> {
                val target = known[a.screen]
                if (target == null) {
                    problems += "$where: la pantalla «${a.screen}» no existe en android_operator/screens/."
                } else {
                    target.filter { it !in a.params }.forEach { problems += "$where: «${a.screen}» necesita el parámetro «$it» en «params»." }
                }
                a.params.forEach { (k, t) -> template(t.raw, "$where.params.$k", names) }
            }
            is Action.OpenChat -> template(a.session.raw, "$where.session", names)
            is Action.OpenOrder -> template(a.order.raw, "$where.order", names)
            is Action.OpenUrl -> {
                template(a.url.raw, "$where.url", names)
                val prefix = a.url.literalPrefix()
                if (!prefix.startsWith("https://") && !prefix.startsWith("tel:")) {
                    problems += "$where.url: tiene que empezar con https:// o tel: (escrito tal cual, no desde un dato)."
                }
            }
            Action.Back -> Unit
            is Action.Refresh -> a.sources.filter { it !in doc.data }.forEach {
                problems += "$where: «$it» no es una fuente de datos de esta pantalla (las que hay: ${doc.data.keys.joinToString()})."
            }
            is Action.SetState -> a.values.forEach { (k, v) ->
                if (k !in stateKeys) problems += "$where: «$k» no está en «state» (las claves que hay: ${stateKeys.joinToString()})."
                (v as? JsonPrimitive)?.takeIf { it.isString }?.let { template(it.content, "$where.values.$k", names) }
            }
            is Action.Call -> {
                if (a.method !in CALL_METHODS) problems += "$where.method: «${a.method}» no vale; usa ${CALL_METHODS.joinToString()}."
                pathProblem(a.path)?.let { problems += "$where.path: $it" }
                template(a.path.raw, "$where.path", names)
                a.query.forEach { (k, t) -> template(t.raw, "$where.query.$k", names) }
                a.body?.let { json(it, "$where.body", names) }
                a.confirm?.let { c ->
                    template(c.title.raw, "$where.confirm.title", names)
                    c.body?.let { template(it.raw, "$where.confirm.body", names) }
                }
                a.success?.let { template(it.raw, "$where.success", names) }
                a.then.forEachIndexed { i, t -> action(t, "$where.then[$i]", names) }
            }
            is Action.Message -> template(a.text.raw, "$where.text", names)
            is Action.Copy -> template(a.text.raw, "$where.text", names)
            is Action.Sequence -> a.actions.forEachIndexed { i, t -> action(t, "$where[$i]", names) }
            is Action.Native -> {
                if (a.name !in Catalog.nativeActions) {
                    problems += "$where: no hay una acción nativa «${a.name}» (las que hay: ${Catalog.nativeActions.keys.joinToString()})."
                } else {
                    val args = a.args as? JsonObject
                    Catalog.nativeActionArgs[a.name].orEmpty().filter { args?.get(it) == null }.forEach {
                        problems += "$where.args: «${a.name}» necesita «$it»."
                    }
                }
                a.args?.let { json(it, "$where.args", names) }
            }
            is Action.AskFirst -> {
                template(a.confirm.title.raw, "$where.title", names)
                a.confirm.body?.let { template(it.raw, "$where.body", names) }
                if (a.then == null) problems += "$where: falta «then» (lo que se hace si el operador acepta)."
                action(a.then, "$where.then", names)
            }
            is Action.If -> {
                template(a.condition.raw, "$where.condition", names)
                if (a.then == null && a.otherwise == null) problems += "$where: falta «then» o «else»."
                action(a.then, "$where.then", names)
                action(a.otherwise, "$where.else", names)
            }
            is Action.Unknown -> problems += "$where: acción desconocida «${a.type}» (las que hay: ${Catalog.actions.keys.joinToString()})."
        }
    }

    private fun json(el: JsonElement, where: String, names: Set<String>) {
        when (el) {
            is JsonObject -> el.forEach { (k, v) -> json(v, "$where.$k", names) }
            is JsonArray -> el.forEachIndexed { i, v -> json(v, "$where[$i]", names) }
            is JsonPrimitive -> if (el.isString) template(el.content, where, names)
        }
    }

    private fun icon(name: String, where: String) {
        if (name !in Catalog.icons) problems += "$where: el ícono «$name» no existe (los que hay: ${Catalog.icons.joinToString()})."
    }

    /** Que la plantilla se lea y que solo use nombres que existen ahí (y claves de `state` declaradas). */
    private fun template(raw: String, where: String, names: Set<String>): Template? {
        val t = try {
            Template.parse(raw)
        } catch (e: TemplateError) {
            problems += "$where: ${e.message}"
            return null
        }
        t.paths().forEach { path ->
            val root = path.first()
            when {
                root !in names -> problems += "$where: «$root» no existe aquí (puedes usar: ${names.sorted().joinToString()})."
                root == "state" && path.size > 1 && path[1] !in stateKeys ->
                    problems += "$where: «state.${path[1]}» no está declarado en «state»."
            }
        }
        return t
    }
}
