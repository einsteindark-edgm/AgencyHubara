"""El pedido que arma el operador con el bot apagado — lógica PURA.

Con un humano al mando el bot no corre turnos: nadie escribe el borrador del
pedido (`order_draft`). Las acciones de la App Operador (el formulario de
envío, el resumen para confirmar, las burbujas) se arman desde ese borrador,
así que con el bot apagado se quedaban sin datos. Caso 2026-10-09: el episodio
tenía el borrador vacío, «Pedir datos de envío» y «Resumen para confirmar»
respondían «faltan datos para esta acción» y la burbuja de colores no salía.

`operator_draft` completa el borrador con lo que pasó con el humano al mando,
SIN escribirlo (el borrador sigue siendo la memoria del bot):

* los productos: la última jugada del operador que nombra uno. El formulario
  que mandó con producto y cantidad (``"operador": True``: el operador ya dio
  las variantes por buenas) gana a una lista de colores o aromas, que solo
  pone el producto en juego cuando el borrador no tiene ninguno;
* los datos de envío: la última respuesta del cliente al formulario
  (``[datos de envío recibidos] city=…; address=…``).

Una jugada del operador posterior a la última escritura del borrador gana;
la respuesta al formulario anterior a ella solo llena lo que falta. Solo
cuenta lo de este episodio (``since_ms``): el formulario de otra compra no es
el de esta.

DEHA: sin I/O ni reloj; el caller lee metadata, ledger y JSONL.
"""
from __future__ import annotations

import copy
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.plugins.chats.shared.chat_events import detect_chat_event
from src.plugins.chats.shared.draft_items import draft_items
from src.plugins.chats.shared.funnel import active_episode

#: Campo del formulario de envío (Flow) → slot del borrador.
FORM_TO_SLOT: dict[str, str] = {
    "city": "ciudad",
    "neighborhood": "barrio",
    "address": "direccion",
    "phone": "telefono",
    "receiver_name": "nombre_recibe",
    "national_id": "cedula",
    "payment_method": "metodo_pago",
}

#: Las acciones que el operador mandó desde la app (`operator_tools.LEDGER_KEY`).
LEDGER_KEY = "operator_tool_actions"

_FORM_TOOL = "request_shipping_details"
_PICKER_TOOL = "present_variant_picker"
_MAX_QUANTITY = 999


def positive_quantity(raw: Any) -> int | None:
    """Cantidad de una jugada: entero 1..999 (la app la manda como texto), o None."""
    if isinstance(raw, bool):
        return None
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= _MAX_QUANTITY else None


def _in_scope(at_ms: Any, since_ms: int | None) -> bool:
    """Sin límite cuenta todo (también un evento viejo sin hora); con límite, solo lo fechado desde ahí."""
    return since_ms is None or (isinstance(at_ms, int) and at_ms >= since_ms)


def _form_items(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Los productos de un formulario que mandó el operador: `{product, quantity}`
    (lo que manda la app) o los `items` nativos de la tool."""
    pairs: list[tuple[Any, Any]] = []
    if args.get("product"):
        pairs = [(args.get("product"), args.get("quantity", 1))]
    elif isinstance(args.get("items"), list):
        pairs = [(i.get("handle"), i.get("quantity")) for i in args["items"] if isinstance(i, dict)]
    items = [
        {"producto": str(handle).strip(), "cantidad": str(quantity), "operador": True}
        for handle, raw in pairs
        if isinstance(handle, str) and handle.strip() and (quantity := positive_quantity(raw))
    ]
    return items if len(items) == len(pairs) else []


def _latest_move(ledger: Sequence[Any], tool: str, since_ms: int | None) -> tuple[int, list[dict[str, Any]]] | None:
    """(cuándo, productos) de la última jugada enviada de `tool` que nombra productos."""
    for entry in reversed(ledger):
        if not isinstance(entry, dict) or entry.get("tool") != tool or entry.get("sent") is False:
            continue
        if not isinstance(entry.get("at_ms"), int) or not _in_scope(entry["at_ms"], since_ms):
            continue
        if not isinstance(entry.get("args"), dict):
            continue
        args = entry["args"]
        if tool == _FORM_TOOL:
            items = _form_items(args)
        else:
            product = args.get("product")
            items = [{"producto": product.strip()}] if isinstance(product, str) and product.strip() else []
        if items:
            return entry["at_ms"], items
    return None


def _event_ms(event: dict[str, Any]) -> int | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        return None


def _form_reply(events: Sequence[Any], since_ms: int | None) -> tuple[int, dict[str, str]] | None:
    """(cuándo, slots) de la última respuesta del cliente al formulario de envío."""
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("role") != "user":
            continue
        parsed = detect_chat_event(event)
        if not parsed or parsed.get("kind") != "shipping_form":
            continue
        at_ms = _event_ms(event)
        if not _in_scope(at_ms, since_ms):
            return None  # la última respuesta es de antes: no hay una de este episodio
        fields = {k: parsed.get(k) for k in FORM_TO_SLOT}
        fields.update({e["key"]: e["value"] for e in parsed.get("extra") or [] if e.get("key") in FORM_TO_SLOT})
        slots = {FORM_TO_SLOT[k]: str(v).strip() for k, v in fields.items() if v and str(v).strip()}
        return (at_ms or 0, slots) if slots else None  # sin hora (historial viejo): solo llena lo que falta
    return None


def operator_draft(
    draft: dict[str, Any] | None,
    *,
    ledger: Sequence[Any],
    events: Sequence[Any],
    since_ms: int | None,
) -> dict[str, Any]:
    """El borrador que ven la app y sus acciones: `{"items": [...], "slots": {...}}`.

    ``draft`` no se toca. ``ledger``: las acciones del operador
    (`operator_tool_actions`). ``events``: el JSONL de la sesión.
    """
    base = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    items = draft_items(base)
    slots = dict(base.get("slots") or {}) if isinstance(base.get("slots"), dict) else {}
    updated = base.get("updated_at_ms") if isinstance(base.get("updated_at_ms"), int) else 0

    form = _latest_move(ledger, _FORM_TOOL, since_ms)
    picker = _latest_move(ledger, _PICKER_TOOL, since_ms)
    if form is not None and (not items or form[0] > updated):
        items = form[1]
    elif picker is not None and not items:
        items = picker[1]

    reply = _form_reply(events, since_ms)
    if reply is not None:
        at_ms, filled = reply
        for key, value in filled.items():
            if at_ms > updated or not slots.get(key):
                slots[key] = value
    return {"items": items, "slots": slots}


#: Lo que el cliente toca en la tarjeta del resumen para cerrar la compra
#: (`flush_ui_intents`: «✅ Confirmar»; el otro botón es «Modificar»).
_CONFIRM_TITLE = "confirmar"


def summary_confirmed(events: Sequence[Any], *, since_ms: int | None) -> bool:
    """¿El cliente tocó «Confirmar» en el ÚLTIMO resumen del pedido de este
    episodio? Un resumen enviado después de la confirmación espera la suya;
    lo de un episodio anterior es otra compra."""
    last_tap: str | None = None  # el último botón que tocó (cuenta ese)
    for event in reversed(events):
        if not isinstance(event, dict) or not _in_scope(_event_ms(event), since_ms):
            return False
        if event.get("role") == "assistant" and event.get("component_kind") == "order_confirmation":
            return last_tap is not None and last_tap.endswith(_CONFIRM_TITLE)
        parsed = detect_chat_event(event) if event.get("role") == "user" else None
        if parsed and parsed.get("kind") == "button_tap" and last_tap is None:
            last_tap = str(parsed.get("title") or "").strip().casefold()
    return False


# ── el pedido de ESTA conversación ──────────────────────────────────────────


def _operator_scope(metadata: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    """(borrador, desde cuándo) de lo que arma el operador: el episodio activo
    o, sin él, lo posterior al cierre del último (el borrador de una compra ya
    registrada no es el de la siguiente)."""
    episode = active_episode(metadata)
    if episode is not None:
        draft = episode.get("order_draft") if isinstance(episode.get("order_draft"), dict) else None
        since = episode.get("started_at_ms")
    else:
        episodes = metadata.get("episodes")
        last = episodes[-1] if isinstance(episodes, list) and episodes and isinstance(episodes[-1], dict) else {}
        draft, since = None, last.get("closed_at_ms")
    return draft, since if isinstance(since, int) else None


def operator_view(metadata: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """El borrador con que trabajan las acciones del operador: el del episodio
    completado con lo que pasó con el humano al mando (el producto que eligió
    en la app, lo que el cliente llenó en el formulario; ver `operator_order`)."""
    draft, since = _operator_scope(metadata)
    ledger = metadata.get(LEDGER_KEY)
    return operator_draft(draft, ledger=ledger if isinstance(ledger, list) else [], events=events, since_ms=since)


def customer_confirmed(metadata: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """¿El cliente cerró la compra en este episodio? Tocó «✅ Confirmar» en el
    último resumen, o el borrador anotó su «sí» (`confirmed_at_ms`)."""
    draft, since = _operator_scope(metadata)
    return isinstance((draft or {}).get("confirmed_at_ms"), int) or summary_confirmed(events, since_ms=since)
