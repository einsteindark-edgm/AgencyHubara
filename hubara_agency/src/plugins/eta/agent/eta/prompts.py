"""Plantillas deterministas del sub-agente ETA (notificador puro).

El ETA NO conversa: emite un aviso por cada cambio de estado de cada pedido, y
nada más (los inbounds los atiende Sales). La matriz de mensajes es finita
—5 estados × ≤3 tipos de pago— y todos los slots (nombre, número, productos,
monto, ventana) llegan ya estructurados desde ``claim_eta_notification_activity``.

Por eso el mensaje se renderiza con una **función pura determinista**
(``render_stage_notification``), no con el LLM. Ventajas frente al path LLM
anterior:

  * **Honestidad garantizada**: el texto del pago se decide por
    ``payment_confirmed`` (pago real) — imposible que "invente" un pago
    confirmado o un nombre ("Hola Cliente") como hacía el LLM.
  * **Testeable byte-a-byte**: se puede afirmar el string EXACTO que recibe el
    cliente (antes era "integración / E2E manual").
  * **Sin costo ni latencia**: cero llamadas al LLM para rellenar una plantilla;
    desaparece toda la maquinaria de cache que existía solo para abaratarlas.

Sin side effects, sin I/O — funciones puras testeables.
"""
from __future__ import annotations

# Etiqueta legible por stage del backend (``src.platform.orders.state``). El
# stage ``new`` no dispara notificación (la primera notificación es ``preparing``).
# Usado también por el path fuera-de-ventana (template Meta) en el workflow.
STAGE_LABELS: dict[str, str] = {
    "preparing": "En preparación",
    "ready": "Listo para envío",
    "shipping": "En camino",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}


def _hola(name: str) -> str:
    """Saludo con nombre o "¡Hola!" a secas. Sin nombre real NUNCA "Hola cliente"."""
    return f"¡Hola {name}!" if name else "¡Hola!"


def _buenas(name: str) -> str:
    return f"¡Buenas noticias {name}!" if name else "¡Buenas noticias!"


def _order_phrase(order_display_id: str, items_label: str) -> str:
    """"#1246 (2× Vela Cruz de Vida)" o "#1246" si no hay productos.

    El cliente no reconoce "#6" a secas, así que cuando hay productos los
    nombramos junto al número; si ``items_label`` viene vacío, NO inventamos.
    """
    return f"{order_display_id} ({items_label})" if items_label else order_display_id


def _window_suffix(delivery_window: str | None) -> str:
    """Frase opcional de estimado de entrega para ``ready``/``shipping``.

    Hoy el backend pasa siempre ``None`` (la ventana específica no se modela
    todavía); el slot queda listo para una HU futura. Si llega "aún no definida"
    (sentinel) tampoco mostramos nada — no inventamos fechas.
    """
    if delivery_window and delivery_window != "aún no definida":
        return f" Estimado de entrega: {delivery_window}."
    return ""


def render_stage_notification(
    *,
    stage: str,
    customer_name: str,
    order_display_id: str,
    total_label: str,
    pay_type: str,
    payment_confirmed: bool = False,
    delivery_window: str | None = None,
    items_label: str = "",
) -> str | None:
    """Renderiza el mensaje EXACTO de una notificación de cambio de estado.

    Determinista y puro: misma entrada → mismo string. El workflow lo envía tal
    cual al cliente (``content`` literal de WhatsApp).

    El texto del pago se elige por el estado REAL del pago, no por la modalidad:
      * ``pay_type == "cod"`` → contra entrega: recuerda el monto.
      * ``payment_confirmed`` (pago real, ``pay_status == "paid"``) → "ya pagado".
      * prepago AÚN sin confirmar → NO menciona el pago (solo el cambio de
        estado). Es el caso del bug 2026-06-11: nuevo→preparación no implica
        pago confirmado, y ``pay_type`` defaultea a "confirmed" (modalidad).

    Saludo sin nombre real → "¡Hola!" a secas (el placeholder "Cliente" de
    Medusa ya lo filtra la activity → ``customer_name`` llega vacío).

    Devuelve ``None`` para un stage desconocido (el workflow lo saltea).
    """
    name = customer_name.strip() if customer_name else ""
    order = _order_phrase(order_display_id, items_label)
    monto = total_label or "el monto"

    if pay_type == "cod":
        pay = "cod"
    elif payment_confirmed:
        pay = "confirmed"
    else:
        pay = "pending"

    if stage == "preparing":
        intro = (
            f"{_hola(name)} Soy tu asistente de seguimiento de Hubara. "
            f"Tu pedido {order}"
        )
        if pay == "confirmed":
            return (
                f"{intro} acaba de entrar en preparación. Tu pago ya está "
                "confirmado, así que cuando llegue solo tienes que recibirlo 🙌 "
                "Te aviso en cada paso."
            )
        if pay == "cod":
            return (
                f"{intro} entró en preparación. Recuerda que es contra entrega: "
                f"pagarás {monto} en efectivo o transferencia cuando lo recibas. "
                "Te aviso en cada paso 🙌"
            )
        return (
            f"{intro} acaba de entrar en preparación. Te aviso en cada paso 🙌"
        )

    if stage == "ready":
        if pay == "cod":
            return (
                f"Tu pedido {order_display_id} ya está empacado y sale a ruta "
                f"muy pronto. 💡 Ten listos {monto} para pagar cuando lo recibas."
                + _window_suffix(delivery_window)
            )
        tail = " Recuerda que ya está pagado." if pay == "confirmed" else ""
        return (
            f"{_buenas(name)} Tu pedido {order_display_id} ya está empacado y "
            f"listo para salir. Te escribo apenas vaya en camino.{tail}"
            + _window_suffix(delivery_window)
        )

    if stage == "shipping":
        if pay == "confirmed":
            body = (
                f"Tu pedido {order} ya va en camino 🚚. Recuerda que está "
                "pagado, así que al recibirlo no tienes que pagar nada. Te aviso "
                "cuando esté por llegar."
            )
        elif pay == "cod":
            body = (
                f"Tu pedido {order} ya va en camino 🚚. Recuerda que al "
                f"recibirlo pagas {monto} al repartidor (efectivo o transferencia)."
            )
        else:
            body = (
                f"Tu pedido {order} ya va en camino 🚚. Te aviso cuando esté "
                "por llegar."
            )
        return body + _window_suffix(delivery_window)

    if stage == "delivered":
        return (
            f"¡Tu pedido {order} fue entregado! 🎉 Esperamos que lo disfrutes. "
            "Si algo no salió como esperabas, escríbenos por aquí y con gusto te "
            "ayudamos 🤍"
        )

    if stage == "cancelled":
        greet = f"Hola {name}, " if name else "Hola, "
        return (
            f"{greet}te confirmo que tu pedido {order_display_id} fue cancelado. "
            "Si tienes alguna duda, escríbenos por aquí y te ayudamos 🤍"
        )

    return None
