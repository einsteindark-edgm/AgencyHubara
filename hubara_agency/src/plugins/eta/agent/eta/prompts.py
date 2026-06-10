"""Strings de negocio puros del sub-agente ETA.

Mantiene los textos fuera del workflow code (igual que ``sales/prompts.py``).
Sin side effects, sin I/O — funciones puras testeables.

CACHE-HIT (objetivo ~100%): la identidad, el tono, los guardrails y las
**plantillas canónicas** de los mensajes viven en el workspace
(``IDENTITY.md`` / ``SOUL.md`` / ``TOOLS.md``), que ``ContextBuilder`` arma como
system prompt byte-estable en TODA sesión y turno → DeepSeek cachea ese prefijo
automáticamente. Por eso el trigger sintético de abajo NO repite las plantillas:
solo entrega los **slots variables** (nombre, número, monto, ventana) en el
mensaje del turno (trailing), que es la única parte que cambia entre llamadas.
"""
from __future__ import annotations

# Etiqueta legible por stage del backend (``src.platform.orders.state``). El
# stage ``new`` no dispara notificación (la primera notificación es ``preparing``).
STAGE_LABELS: dict[str, str] = {
    "preparing": "En preparación",
    "ready": "Listo para envío",
    "shipping": "En camino",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}


def build_stage_notification_turn(
    *,
    stage: str,
    customer_name: str,
    order_display_id: str,
    total_label: str,
    pay_type: str,
    delivery_window: str | None,
    items_label: str = "",
) -> str:
    """Mensaje sintético del turno que dispara una notificación de cambio de estado.

    Es el contenido (rol ``user``) que recibe el agente cuando un pedido cambia
    de estado. Le dice QUÉ pasó + los datos; el agente elige la plantilla
    correspondiente de su workspace y la rellena. Prefijo estable + slots al
    final = cache-friendly (ver docstring del módulo).
    """
    stage_label = STAGE_LABELS.get(stage, stage)
    window = delivery_window if delivery_window else "aún no definida"
    pay_hint = (
        "contra entrega (el cliente paga al recibir; recuérdale el monto)"
        if pay_type == "cod"
        else "pago ya confirmado (el cliente NO paga al recibir; recuérdale que ya está pagado)"
    )
    return (
        "[EVENTO DEL SISTEMA — no es un mensaje del cliente, es tu disparador]\n"
        "El pedido de este cliente acaba de cambiar de estado. Envía AHORA, en "
        "un solo mensaje de WhatsApp, la notificación que corresponde a este "
        "estado, usando la plantilla de tu workspace para esta combinación de "
        "estado + tipo de pago. Rellena los datos con los valores de abajo. No "
        "agregues nada fuera de tu rol de notificación; no vuelvas a presentarte "
        "si ya venías notificando a este cliente.\n"
        "\n"
        "Datos para esta notificación:\n"
        f"- estado_nuevo: {stage_label} (clave interna: {stage})\n"
        f"- nombre_cliente: {customer_name}\n"
        f"- numero_pedido: {order_display_id}\n"
        f"- productos: {items_label or '(no disponibles — NO los inventes, omite mencionarlos)'}\n"
        f"- monto_total: {total_label}\n"
        f"- tipo_pago: {pay_type} — {pay_hint}\n"
        f"- ventana_entrega: {window}"
    )
