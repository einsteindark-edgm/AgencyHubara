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


# max_length del slot `order_reference` en el spec `order_status_utility_v2`
# (catalog.yaml). El test cross-checkea contra el registry para detectar drift.
_ORDER_REFERENCE_MAX_LEN = 60


def format_cop(amount: int | None) -> str:
    """Monto COP al estilo del total del pedido: ``$ 215.000`` (miles con
    punto). Cero / ``None`` → "" (sin monto no se escribe nada)."""
    if not amount:
        return ""
    return "$ " + f"{int(amount):,}".replace(",", ".")


def _positive_cop(raw: object) -> int | None:
    """Entero COP > 0 o ``None`` (descarta bool, cero, negativos y basura)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def shipping_breakdown(
    shipping_cost: int | None, order_total_cop: int | None
) -> list[tuple[str, str]] | None:
    """Desglose de "en camino" cuando el operador informó el valor del envío.

    ``[("Valor del pedido", "$ 50.000"), ("Valor del envío", "$ 12.000"),
    ("Total", "$ 62.000")]``. Sin total del pedido conocido (Medusa caído)
    queda solo el envío: no inventamos el valor del pedido ni el total. Sin
    envío → ``None`` (el mensaje es el de siempre)."""
    envio = _positive_cop(shipping_cost)
    if envio is None:
        return None
    pedido = _positive_cop(order_total_cop)
    if pedido is None:
        return [("Valor del envío", format_cop(envio))]
    return [
        ("Valor del pedido", format_cop(pedido)),
        ("Valor del envío", format_cop(envio)),
        ("Total", format_cop(pedido + envio)),
    ]


def build_status_template_variables(
    stage: str,
    facts: dict,
    tracking_url: str | None = None,
    shipping_cost: int | None = None,
) -> dict[str, str]:
    """Variables del template fuera-de-ventana (``order_status_utility_v2``).

    Pura y determinista — el workflow la llama tal cual (R-DET OK). Garantiza
    los invariantes que Meta exige de un template param:

      * **Sin nombre**: el template v2 no saluda por nombre (incidente
        2026-07-21: el placeholder Medusa dejaba el slot vacío y Meta rechaza
        params "" con 131008 → notificación perdida).
      * **Nunca vacío**: ``order_reference`` cae a "tu pedido" sin datos del
        pedido; ``status_label`` cae al stage crudo si no está en la matriz.
      * **Dentro de max_length**: el spec limita ``order_reference`` a 60.

    Los nombres/orden matchean el spec del catálogo (params posicionales).
    """
    reference = facts.get("order_display_id") or "tu pedido"
    items_label = facts.get("items_label") or ""
    if items_label:
        # El cliente no sabe qué es "#6" — nombramos los productos en el
        # slot de referencia.
        reference = f"{reference} ({items_label})"
    status_label = STAGE_LABELS.get(stage, stage)
    url = _clean_tracking_url(tracking_url)
    detalle = (
        shipping_breakdown(shipping_cost, facts.get("total_cop"))
        if stage == "shipping"
        else None
    )
    if stage == "shipping" and (url or detalle):
        # Fuera de ventana el ÚNICO canal es este template: el desglose
        # (pedido, envío, total) y el link de la guía viajan dentro del slot
        # de estado ("...está en camino. Valor del pedido: $ 50.000, valor
        # del envío: $ 12.000, total: $ 62.000. Sigue tu envío aquí: https://…
        # . Si quieres más información…"). El catálogo declara max_length
        # holgado para este slot; sin saltos de línea ni 4+ espacios (Meta
        # los rechaza en params).
        status_label = "en camino"
        if detalle:
            partes = [f"{k}: {v}" for k, v in detalle]
            partes[1:] = [p[0].lower() + p[1:] for p in partes[1:]]
            status_label += ". " + ", ".join(partes)
        if url:
            status_label += f". Sigue tu envío aquí: {url}"
    return {
        "order_reference": reference[:_ORDER_REFERENCE_MAX_LEN],
        "status_label": status_label,
    }


def _clean_tracking_url(tracking_url: str | None) -> str:
    """URL de guía normalizada: sin espacios en los bordes; vacío → ""."""
    return (tracking_url or "").strip()


def _tracking_suffix(tracking_url: str | None) -> str:
    """Bloque final con el link de la guía para el mensaje "en camino".

    Va en su propia burbuja (``\n\n`` = chunk separado en
    ``send_message_to_session``) y con la URL cruda al final de la línea:
    WhatsApp la linkifica sola, el cliente la toca y abre el rastreo. Nada de
    markdown ni paréntesis pegados a la URL (rompen la detección del link).
    """
    url = _clean_tracking_url(tracking_url)
    return f"\n\nPuedes seguir tu envío aquí: {url}" if url else ""


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
    tracking_url: str | None = None,
    shipping_cost: int | None = None,
    order_total_cop: int | None = None,
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

    ``tracking_url`` (opcional, lo adjunta el operador al mover el pedido a
    "en camino") se agrega SOLO al mensaje de ``shipping`` como link tappable
    al final; las demás etapas lo ignoran.

    ``shipping_cost`` (COP entero, opcional; lo escribe el operador al marcar
    "en camino") se informa SOLO en ``shipping`` como desglose separado del
    pedido — "Valor del pedido" (``order_total_cop``, total vivo del pedido),
    "Valor del envío" y "Total" (la suma), una línea cada uno dentro de la
    misma burbuja. Contra entrega cobra el total; el pagado dice que el valor
    del pedido ya está pagado (ya NO "no tienes que pagar nada").

    2026-09-22: ni ``preparing`` ni ``ready`` contra entrega recuerdan el monto
    ("pagarás $ X…" / "Ten listos $ X…") — el precio se menciona recién en
    "en camino".

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
        # Contra entrega o prepago sin confirmar: solo el cambio de estado,
        # sin precio (el monto se recuerda recién en "en camino").
        return (
            f"{intro} acaba de entrar en preparación. Te aviso en cada paso 🙌"
        )

    if stage == "ready":
        # 2026-09-22: sin monto tampoco acá (contra entrega = mismo aviso que
        # el prepago sin confirmar); el precio se recuerda en "en camino".
        tail = " Recuerda que ya está pagado." if pay == "confirmed" else ""
        return (
            f"{_buenas(name)} Tu pedido {order_display_id} ya está empacado y "
            f"listo para salir. Te escribo apenas vaya en camino.{tail}"
            + _window_suffix(delivery_window)
        )

    if stage == "shipping":
        head = f"Tu pedido {order} ya va en camino 🚚."
        detalle = shipping_breakdown(shipping_cost, order_total_cop)
        if detalle:
            # Una línea por concepto, misma burbuja ("\n" simple; "\n\n"
            # partiría el mensaje en chunks).
            lines = [head, *(f"{k}: {v}" for k, v in detalle)]
            por_cobrar = detalle[-1][1] if len(detalle) == 3 else monto
            if pay == "confirmed":
                lines.append(
                    "El valor del pedido ya está pagado. Te aviso cuando esté por llegar."
                )
            elif pay == "cod":
                lines.append(
                    f"Recuerda que al recibirlo pagas {por_cobrar} al repartidor "
                    "(efectivo o transferencia)."
                )
            else:
                lines.append("Te aviso cuando esté por llegar.")
            return (
                "\n".join(lines)
                + _window_suffix(delivery_window)
                + _tracking_suffix(tracking_url)
            )
        if pay == "confirmed":
            body = (
                f"{head} Recuerda que está pagado, así que al recibirlo no "
                "tienes que pagar nada. Te aviso cuando esté por llegar."
            )
        elif pay == "cod":
            body = (
                f"{head} Recuerda que al recibirlo pagas {monto} al repartidor "
                "(efectivo o transferencia)."
            )
        else:
            body = f"{head} Te aviso cuando esté por llegar."
        return body + _window_suffix(delivery_window) + _tracking_suffix(tracking_url)

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
