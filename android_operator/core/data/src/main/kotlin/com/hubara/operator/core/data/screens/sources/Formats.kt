package com.hubara.operator.core.data.screens.sources

import com.hubara.operator.core.data.repo.ChatView
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderRef
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.model.ShippingForm
import com.hubara.operator.core.model.Template
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// Las fuentes del teléfono entregan los datos ya listos para mostrar: el JSON de cada pantalla solo los enlaza. Estas
// reglas vivían en las pantallas nativas (bandeja, incendios, encabezado del chat, hoja de plantillas).

/** El teléfono en formato legible. En notificaciones y widget NO se muestra (privacidad). */
fun displayPhone(raw: String): String {
    val digits = raw.filter(Char::isDigit)
    return when {
        digits.isEmpty() -> "Cliente"
        digits.length == 12 && digits.startsWith("57") ->
            "+57 ${digits.substring(2, 5)} ${digits.substring(5, 8)} ${digits.substring(8)}"
        else -> raw
    }
}

/** Lo que se ve del último mensaje: el formulario de envío en palabras, no `clave=valor`. */
fun inboxPreview(raw: String?): String? = if (ShippingForm.parse(raw) != null) "Datos de envío recibidos" else raw

/** Quién atiende, en qué va y si tiene pedido. Sin códigos del backend. */
fun conversationSubtitle(route: Route, tag: String, orderRef: OrderRef?): String {
    val who = if (route == Route.HUMAN) "Humano" else "Bot"
    val stage = TAG_LABELS[tag]?.takeIf { it != who }
    val order = orderRef?.let { ref -> ref.displayId?.let { "Pedido #$it" } ?: "Pedido" }
    return listOfNotNull(who, stage, order).joinToString(" · ")
}

/** Las etiquetas que escribe el bot, con los nombres de la bandeja del dashboard web (`chat/api.ts`). */
private val TAG_LABELS = mapOf(
    "INTERESADO" to "Interesado",
    "COMPRA_EXITOSA" to "Cliente",
    "CLIENTE" to "Cliente",
    "RECHAZO" to "Frío",
    "FRÍO" to "Frío",
    "FRIO" to "Frío",
    "SIN_RESPUESTA" to "Sin respuesta",
    "CONFIRMADO_SIN_DATOS" to "Pendiente",
    "CONFIRMADO_PAGO_PENDIENTE" to "Pendiente",
    "NO_ETIQUETADO" to "Pendiente",
    "PENDIENTE" to "Pendiente",
    "REMARKETING" to "Remarketing",
    "HUMANO" to "Humano",
)

/**
 * Un chat de la bandeja. `unread`, `human`, `has_order` y `all` son verdadero/falso para filtrar con
 * `where:state.filtro` (cada filtro es el nombre de un campo).
 */
fun conversationJson(c: Conversation, unseen: Int): JsonObject = buildJsonObject {
    put("session_id", c.sessionId.raw)
    put("title", c.customerName ?: displayPhone(c.phone))
    put("name", c.customerName)
    put("phone", displayPhone(c.phone))
    put("detail", listOfNotNull(c.customerName?.let { displayPhone(c.phone) }, conversationSubtitle(c.route, c.tag, c.orderRef)).joinToString(" · "))
    put("preview", inboxPreview(c.lastMessagePreview))
    put("unseen", unseen)
    put("unseen_label", when {
        unseen <= 0 -> ""
        unseen == 1 -> "1 mensaje sin leer"
        else -> "$unseen mensajes sin leer"
    })
    put("unread", unseen > 0)
    put("human", c.route == Route.HUMAN)
    put("has_order", c.orderRef != null)
    put("all", true)
    put("time_ms", c.lastUpdatedMs)
}

/** Un incendio con su etiqueta («CHAT · GRAVE · EMPEORA») y a dónde lleva: `opens` = `order`, `chat` o vacío. */
fun fireJson(f: Fire): JsonObject = buildJsonObject {
    val order = (f.subject as? FireSubject.Order)?.orderId
    val session = f.subject.sessionId
    val isOrder = f.subject is FireSubject.Order
    put("fire_id", f.id.raw)
    put("kind", if (isOrder) "order" else "chat")
    put("severity", f.severity.name.lowercase())
    put(
        "overline",
        listOfNotNull(if (isOrder) "ORDEN" else "CHAT", f.severity.name, if (f.gettingWorse) "EMPEORA" else null).joinToString(" · "),
    )
    put("title", f.title)
    put("subtitle", f.subtitle)
    put("getting_worse", f.gettingWorse)
    put("session_id", session?.raw)
    put("order_id", order?.raw)
    put("opens", when {
        f.primaryAction.name == "open_order" && order != null -> "order"
        session != null -> "chat"
        else -> ""
    })
    put("all", true)
    put("grave", f.severity == Severity.GRAVE)
    put("chat", !isOrder)
    put("order", isOrder)
}

/** El encabezado de un chat: nombre (o número), quién atiende y el botón del pedido. */
fun chatJson(view: ChatView, nowMs: Long): JsonObject = buildJsonObject {
    val human = view.route == Route.HUMAN
    put("session_id", view.sessionId.raw)
    put("title", view.customerName ?: displayPhone(view.phone))
    put("subtitle", listOfNotNull(view.customerName?.let { displayPhone(view.phone) }, if (human) "Tú atiendes" else "El bot atiende").joinToString(" · "))
    put("name", view.customerName)
    put("phone", displayPhone(view.phone))
    put("human", human)
    put("window_open", view.windowExpiresAtMs?.let { it > nowMs } ?: true)
    val ref = view.orderRef
    put("order_id", ref?.orderId?.raw.orEmpty())
    put("order_label", when {
        ref == null -> ""
        ref.count > 1 -> "Pedidos · ${ref.count}"
        else -> ref.displayId?.let { "Pedido #$it" } ?: "Pedido"
    })
}

/**
 * Una plantilla aprobada. `preview_body` cambia los huecos de WhatsApp (`{{1}}`, `{{2}}`) por el nombre de cada variable
 * para que la vista previa se arme con `{{t.preview_body | fill:form:t.fallback}}`.
 */
fun templateJson(t: Template): JsonObject = buildJsonObject {
    put("name", t.name)
    put("title", t.title)
    put("label", t.label)
    put("body", t.body)
    var preview = t.body.orEmpty()
    t.variables.forEachIndexed { i, v -> preview = preview.replace("{{${i + 1}}}", "{{${v.name}}}") }
    put("preview_body", preview)
    put("fallback", JsonObject(t.variables.associate { it.name to JsonPrimitive("[${it.description ?: it.name}]") }))
    put("needs_image", t.needsImage)
    put("is_default", t.isDefault)
    put("variables", JsonArray(t.variables.map { v ->
        buildJsonObject {
            put("name", v.name)
            put("label", v.description ?: v.name)
            put("max_length", v.maxLength?.let(::JsonPrimitive) ?: JsonNull)
        }
    }))
    put("variable_names", JsonArray(t.variables.map { JsonPrimitive(it.name) }))
}
