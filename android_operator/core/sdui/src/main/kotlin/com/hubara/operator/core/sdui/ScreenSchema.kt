package com.hubara.operator.core.sdui

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Lo que sale del [Catalog] para quien escribe pantallas: el esquema JSON que usa VS Code (autocompletado, ayuda al
 * pasar el mouse y errores mientras escribes, con `"$schema": "./screen.schema.json"`) y la referencia `CATALOGO.md`.
 * `ScreensRepoTest` exige que los archivos del repo estén al día con esto.
 */
object ScreenSchema {
    private val pretty = Json { prettyPrint = true; prettyPrintIndent = "  " }

    private const val NOTE = "^[_$]"

    fun screen(): String = pretty.encodeToString(JsonElement.serializer(), screenSchema()) + "\n"

    fun app(): String = pretty.encodeToString(JsonElement.serializer(), appSchema()) + "\n"

    // ── Esquema de una pantalla ──────────────────────────────────────────────────────────────────

    private fun screenSchema(): JsonObject = buildJsonObject {
        put("\$schema", "http://json-schema.org/draft-07/schema#")
        put("title", "Pantalla de la App Operador")
        put("description", "Una pantalla definida por el servidor. Guía: android_operator/screens/README.md · Referencia: CATALOGO.md")
        put("type", "object")
        putJsonArray("required") { add("schema"); add("id"); add("body") }
        notes()
        put("additionalProperties", false)
        putJsonObject("properties") {
            put("\$schema", obj("type" to "string"))
            put("schema", obj("const" to 1, "description" to "Versión de la forma del archivo (siempre 1)."))
            put("id", obj("type" to "string", "pattern" to "^[a-z0-9_]{1,64}$", "description" to "Igual al nombre del archivo, sin .json."))
            put("title", obj("type" to "string", "description" to "Título del encabezado. Acepta {{ … }}."))
            put("subtitle", obj("type" to "string", "description" to "Segunda línea del encabezado."))
            put("avatar", obj("type" to "string", "description" to "Nombre para un avatar junto al título."))
            put("avatar_seed", obj("type" to "string", "description" to "Valor estable para el color del avatar."))
            put("layout", obj("enum" to Catalog.layouts.toList(), "description" to "list (se desliza, por defecto) o fill (llena la pantalla: el chat)."))
            put("requires", obj("type" to "integer", "minimum" to 1, "maximum" to Catalog.VERSION, "description" to "Versión del catálogo que necesita (por defecto 1). Una app más vieja pide actualizarse."))
            put("params", obj("type" to "array", "items" to obj("type" to "string"), "description" to "Parámetros obligatorios: {{params.x}}."))
            put("state", obj("type" to "object", "description" to "Valores que la pantalla cambia (un filtro), con su valor inicial: {{state.x}}."))
            put("data", buildJsonObject {
                put("type", "object")
                put("description", "Fuentes de datos: cada una es un GET a nuestro backend y se lee por su id ({{pedidos.orders}}).")
                put("additionalProperties", source())
            })
            put("computed", obj("type" to "object", "additionalProperties" to obj("type" to "string"),
                "description" to "Valores calculados con nombre, en orden: \"validos\": \"{{pedidos.orders | where_not:'is_test'}}\"."))
            put("topActions", obj("type" to "array", "items" to buildJsonObject {
                put("type", "object")
                putJsonArray("required") { add("icon"); add("label"); add("action") }
                putJsonObject("properties") {
                    put("icon", iconSchema())
                    put("label", obj("type" to "string", "description" to "Lo lee TalkBack; en chip y menú es el texto."))
                    put("style", obj("enum" to Catalog.topActionStyles.toList(), "description" to "icon (por defecto), chip (botón con texto) o menu (renglón de «Más opciones»)."))
                    put("visible", obj("type" to listOf("string", "array"), "description" to "Se muestra solo si da verdadero."))
                    put("action", ref("action"))
                }
            }, "description" to "Íconos de la barra superior."))
            put("fab", buildJsonObject {
                put("type", "object")
                put("description", "Botón flotante abajo a la derecha.")
                putJsonArray("required") { add("label"); add("action") }
                putJsonObject("properties") {
                    put("label", obj("type" to "string"))
                    put("icon", iconSchema())
                    put("action", ref("action"))
                }
            })
            put("body", obj("type" to "array", "items" to ref("node"), "description" to "Los componentes, de arriba abajo."))
        }
        putJsonObject("definitions") {
            put("node", nodeSchema())
            put("action", actionSchema())
        }
    }

    private fun source() = buildJsonObject {
        put("type", "object")
        notes()
        put("additionalProperties", false)
        putJsonObject("properties") {
            put("get", obj("type" to "string", "pattern" to "^/api/", "description" to "Ruta de nuestro backend, empieza con /api/. Acepta {{params.x}} y {{state.x}}."))
            put("app", obj("enum" to Catalog.appSources.keys.toList(), "description" to Catalog.appSources.entries.joinToString(" · ") { "${it.key}: ${it.value}" }))
            put("params", obj("type" to "object", "additionalProperties" to obj("type" to "string"), "description" to "Parámetros de una fuente del teléfono."))
            put("query", obj("type" to "object", "additionalProperties" to obj("type" to "string"), "description" to "Parámetros de la consulta; los vacíos no se mandan."))
            put("optional", obj("type" to "boolean", "description" to "Si falla, la pantalla igual se muestra."))
            put("refreshOn", obj("type" to "array", "items" to obj("enum" to Catalog.eventDomains.sorted()), "description" to "Avisos del servidor que la vuelven a pedir."))
            put("every", obj("type" to "integer", "minimum" to 15, "description" to "Cada cuántos segundos se vuelve a pedir mientras se ve."))
        }
    }

    private fun nodeSchema() = buildJsonObject {
        put("type", "object")
        putJsonArray("required") { add("type") }
        putJsonObject("properties") {
            put("type", obj("enum" to Catalog.components.keys.toList(), "description" to "El componente (ver CATALOGO.md)."))
        }
        // Una rama por componente: sus propiedades, las comunes y nada más (un «txt» por «text» se marca en rojo).
        putJsonArray("allOf") {
            Catalog.components.values.forEach { spec ->
                add(buildJsonObject {
                    put("if", obj("properties" to obj("type" to obj("const" to spec.type)), "required" to listOf("type")))
                    put("then", componentSchema(spec))
                })
            }
        }
    }

    private fun componentSchema(spec: ComponentSpec) = buildJsonObject {
        put("description", spec.doc)
        val required = spec.props.filter { it.required }.map { it.name } + if (spec.actionRequired) listOf("action") else emptyList()
        if (required.isNotEmpty()) putJsonArray("required") { required.forEach { add(it) } }
        notes()
        put("additionalProperties", false)
        putJsonObject("properties") {
            put("type", obj("const" to spec.type))
            put("id", obj("type" to "string"))
            put("visible", obj("type" to listOf("string", "array"), "description" to "Se pinta solo si da verdadero (con varias, todas)."))
            put("weight", obj("type" to "number", "description" to "Dentro de un «row»: ocupa el espacio que sobra."))
            if (spec.tappable) put("action", ref("action"))
            if (spec.container) put("children", obj("type" to "array", "items" to ref("node")))
            if (spec.list) {
                put("as", obj("type" to "string", "description" to "Nombre de cada elemento (por defecto item)."))
                put("item", ref("node"))
                put("empty", ref("node"))
            }
            spec.props.forEach { put(it.name, propSchema(it)) }
        }
    }

    private fun propSchema(p: PropSpec): JsonObject = when (p.kind) {
        PropKind.TEXT -> obj("type" to listOf("string", "number", "boolean"), "description" to p.doc)
        PropKind.BOOL -> obj("type" to listOf("boolean", "string"), "description" to p.doc)
        PropKind.NUMBER -> obj("type" to "number", "description" to p.doc)
        PropKind.ENUM -> obj("anyOf" to listOf(obj("enum" to p.values), TEMPLATE), "description" to p.doc)
        PropKind.ICON -> iconSchema(p.doc)
        PropKind.LIST -> obj("type" to listOf("string", "array"), "description" to p.doc)
        PropKind.OPTIONS -> obj(
            "anyOf" to listOf(
                obj("type" to "array", "items" to obj("type" to "object", "required" to listOf("value", "label"))),
                TEMPLATE,
            ),
            "description" to p.doc,
        )
        PropKind.BIND -> obj("type" to "string", "pattern" to "^(state|form)\\.", "description" to p.doc)
        PropKind.ACTION -> obj("allOf" to listOf(ref("action")), "description" to p.doc)
    }

    private val TEMPLATE = obj("type" to "string", "pattern" to "\\{\\{")

    private fun iconSchema(doc: String = "Ícono del catálogo.") = obj("anyOf" to listOf(obj("enum" to Catalog.icons.toList()), TEMPLATE), "description" to doc)

    private fun actionSchema() = buildJsonObject {
        put("description", "Lo que hace un toque. Una lista [ … ] ejecuta varias en orden.")
        putJsonArray("anyOf") {
            add(obj("type" to "array", "items" to ref("action")))
            add(buildJsonObject {
                put("type", "object")
                putJsonArray("required") { add("type") }
                notes()
                put("additionalProperties", false)
                putJsonObject("properties") {
                    put("type", obj("enum" to Catalog.actions.keys.toList(), "description" to Catalog.actions.entries.joinToString(" · ") { "${it.key}: ${it.value}" }))
                    put("screen", obj("type" to "string", "description" to "navigate: id de la pantalla."))
                    put("params", obj("type" to "object", "additionalProperties" to obj("type" to "string"), "description" to "navigate: parámetros."))
                    put("sheet", obj("type" to "boolean", "description" to "navigate: abrir como hoja inferior."))
                    put("session", obj("type" to "string", "description" to "open_chat: id de la conversación (wa_…)."))
                    put("order", obj("type" to "string", "description" to "open_order: id del pedido."))
                    put("url", obj("type" to "string", "pattern" to "^(https://|tel:)", "description" to "open_url: https:// o tel:."))
                    put("data", obj("type" to "array", "items" to obj("type" to "string"), "description" to "refresh: qué fuentes (vacío = todas)."))
                    put("values", obj("type" to "object", "description" to "set_state: claves de state y su valor nuevo."))
                    put("method", obj("enum" to listOf("GET", "POST", "PUT", "PATCH", "DELETE"), "description" to "call: método."))
                    put("path", obj("type" to "string", "pattern" to "^/api/", "description" to "call: ruta de nuestro backend."))
                    put("query", obj("type" to "object", "additionalProperties" to obj("type" to "string")))
                    put("body", obj("description" to "call: cuerpo JSON; los textos aceptan {{ … }} (una expresión sola conserva su tipo)."))
                    put("confirm", buildJsonObject {
                        put("type", "object")
                        put("description", "call: pide confirmación antes de llamar.")
                        putJsonArray("required") { add("title") }
                        putJsonObject("properties") {
                            put("title", obj("type" to "string"))
                            put("body", obj("type" to "string"))
                            put("accept", obj("type" to "string"))
                            put("dismiss", obj("type" to "string"))
                        }
                    })
                    put("success", obj("type" to "string", "description" to "call: aviso si salió bien."))
                    put("then", obj("anyOf" to listOf(ref("action"), obj("type" to "array", "items" to ref("action"))), "description" to "call: lo que sigue si salió bien."))
                    put("text", obj("type" to "string", "description" to "message / copy: el texto."))
                    put("live", obj("type" to "boolean", "description" to "open_chat: en la pila de Incendios."))
                    put("name", obj("enum" to Catalog.nativeActions.keys.toList(), "description" to Catalog.nativeActions.entries.joinToString(" · ") { "${it.key}: ${it.value}" }))
                    put("args", obj("type" to "object", "description" to "native: sus argumentos; los textos aceptan {{ … }}."))
                    put("title", obj("type" to "string", "description" to "confirm: la pregunta."))
                    put("accept", obj("type" to "string", "description" to "confirm: el botón de aceptar."))
                    put("dismiss", obj("type" to "string", "description" to "confirm: el botón de cancelar."))
                    put("condition", obj("type" to "string", "description" to "if: la condición ({{ … }})."))
                    put("else", ref("action"))
                }
            })
        }
    }

    private fun appSchema(): JsonObject = buildJsonObject {
        put("\$schema", "http://json-schema.org/draft-07/schema#")
        put("title", "Manifiesto de la App Operador")
        put("description", "Las pestañas de la barra de abajo, en orden (la primera es la de inicio). Se aplican la próxima vez que se abre la app.")
        put("type", "object")
        putJsonArray("required") { add("schema"); add("tabs") }
        notes()
        put("additionalProperties", false)
        putJsonObject("properties") {
            put("\$schema", obj("type" to "string"))
            put("schema", obj("const" to 1))
            put("tabs", obj("type" to "array", "minItems" to MIN_TABS, "maxItems" to MAX_TABS, "items" to buildJsonObject {
                put("type", "object")
                putJsonArray("required") { add("screen"); add("label"); add("icon") }
                put("additionalProperties", false)
                putJsonObject("properties") {
                    put("screen", obj("type" to "string", "pattern" to "^[a-z0-9_]{1,64}$", "description" to "Una pantalla sin parámetros."))
                    put("label", obj("type" to "string"))
                    put("icon", iconSchema())
                    put("icon_selected", iconSchema("Ícono con la pestaña elegida."))
                    put("badge", obj("type" to "string", "description" to "Número sobre el ícono, de una fuente del teléfono: {{radar | count}}."))
                }
            }))
        }
    }

    private fun kotlinx.serialization.json.JsonObjectBuilder.notes() {
        put("patternProperties", obj(NOTE to obj("description" to "Nota para quien lee el archivo (la app la ignora).")))
    }

    private fun ref(name: String) = obj("\$ref" to "#/definitions/$name")

    private fun obj(vararg pairs: Pair<String, Any>): JsonObject = JsonObject(pairs.associate { (k, v) -> k to json(v) })

    private fun json(v: Any): JsonElement = when (v) {
        is JsonElement -> v
        is String -> JsonPrimitive(v)
        is Number -> JsonPrimitive(v)
        is Boolean -> JsonPrimitive(v)
        is List<*> -> buildJsonArray { v.forEach { add(json(it!!)) } }
        else -> error("tipo no soportado: $v")
    }

    // ── Referencia en Markdown ───────────────────────────────────────────────────────────────────

    fun reference(): String = buildString {
        appendLine("<!-- Generado desde Catalog.kt y Filters.kt: no lo edites a mano. ./gradlew :core:sdui:test -PupdateScreens=true -->")
        appendLine("# Catálogo de pantallas del servidor (versión ${Catalog.VERSION})")
        appendLine()
        appendLine("Lo que esta versión de la App Operador sabe pintar y hacer. La guía para crear una pantalla está en")
        appendLine("[README.md](README.md). Todo texto acepta `{{ … }}`.")
        appendLine()
        appendLine("## Componentes")
        appendLine()
        Catalog.components.values.forEach { spec ->
            appendLine("### `${spec.type}`")
            appendLine()
            appendLine(spec.doc)
            val extras = buildList {
                if (spec.container) add("lleva `children`")
                if (spec.list) add("`items` + `item` (+ `as`, `empty`) o `children` fijos")
                if (spec.actionRequired) add("necesita `action`") else if (spec.tappable) add("se puede tocar (`action`)")
            }
            if (spec.native) extras.toMutableList()
            if (extras.isNotEmpty() || spec.native) {
                appendLine()
                appendLine("_${(extras + listOfNotNull(if (spec.native) "pieza nativa de la app" else null)).joinToString(" · ").replaceFirstChar { it.uppercase() }}._")
            }
            if (spec.props.isNotEmpty()) {
                appendLine()
                appendLine("| Propiedad | Tipo | Qué es |")
                appendLine("|---|---|---|")
                spec.props.forEach { p ->
                    val kind = when (p.kind) {
                        PropKind.ENUM -> p.values.joinToString(" · ") { "`$it`" }
                        PropKind.ICON -> "ícono"
                        PropKind.TEXT -> "texto"
                        PropKind.BOOL -> "sí/no"
                        PropKind.NUMBER -> "número"
                        PropKind.LIST -> "lista"
                        PropKind.OPTIONS -> "opciones"
                        PropKind.BIND -> "`state.x` / `form.x`"
                        PropKind.ACTION -> "acción"
                    }
                    appendLine("| `${p.name}`${if (p.required) " **(obligatoria)**" else ""} | $kind | ${p.doc.replace("|", "\\|")} |")
                }
            }
            appendLine()
        }
        appendLine("Todos aceptan además `visible` (se pinta solo si da verdadero) y, dentro de un `row`, `weight`.")
        appendLine()
        appendLine("## Acciones")
        appendLine()
        appendLine("| Acción | Qué hace |")
        appendLine("|---|---|")
        Catalog.actions.forEach { (name, doc) -> appendLine("| `$name` | ${doc.replace("|", "\\|")} |") }
        appendLine()
        appendLine("Una lista `[ {…}, {…} ]` en `action` ejecuta varias en orden.")
        appendLine()
        appendLine("### Acciones nativas (`{\"type\": \"native\", \"name\": …, \"args\": {…}}`)")
        appendLine()
        appendLine("| Nombre | Qué hace |")
        appendLine("|---|---|")
        Catalog.nativeActions.forEach { (name, doc) -> appendLine("| `$name` | ${doc.replace("|", "\\|")} |") }
        appendLine()
        appendLine("## Fuentes de datos del teléfono (`\"app\": …`)")
        appendLine()
        appendLine("| Fuente | Qué trae |")
        appendLine("|---|---|")
        Catalog.appSources.forEach { (name, doc) -> appendLine("| `$name` | ${doc.replace("|", "\\|")} |") }
        appendLine()
        appendLine("El estado de cada fuente se lee con `{{status.<fuente>.loading}}` y `{{status.<fuente>.failed}}`.")
        appendLine()
        appendLine("## Filtros de `{{ valor | filtro:arg }}`")
        appendLine()
        Filters.catalog().forEach { (group, filters) ->
            appendLine("### $group")
            appendLine()
            appendLine("| Filtro | Qué hace |")
            appendLine("|---|---|")
            filters.forEach { (name, doc) -> appendLine("| `$name` | ${doc.replace("|", "\\|")} |") }
            appendLine()
        }
        appendLine("## Íconos")
        appendLine()
        appendLine(Catalog.icons.joinToString(" · ") { "`$it`" })
        appendLine()
        appendLine("## Eventos para `refreshOn`")
        appendLine()
        appendLine(Catalog.eventDomains.sorted().joinToString(" · ") { "`$it`" })
    }
}
