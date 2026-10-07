"""Markers del historial → eventos estructurados para el panel del chat.

El JSONL de una sesión guarda cada envío no-textual como una LÍNEA DE TEXTO
pensada para el LLM: `[el cliente tocó el botón: Ver catálogo]`,
`🔘 El bot envió botones: A · B — con el mensaje: «…»`. El dashboard las pintaba
tal cual, y el operador tenía que decodificar a mano qué fue un botón, qué
escribió la persona y qué describió la IA.

Este módulo le devuelve la forma al mensaje: proyecta el marker a un evento
tipado (`bot_buttons`, `button_tap`, `customer_photo`, `reaction`,
`shipping_form`) que el
frontend pinta como lo que fue. `None` = no hay nada que proyectar y el
mensaje se sigue pintando como hoy.

**Por qué parsear y no persistir campos nuevos al escribir**: el historial ya
escrito (todas las conversaciones del vault) se ve con la forma nueva sin
migración ni reprocesamiento. Lo único irrecuperable es el emoji de las
reacciones VIEJAS — se perdía al traducir, y no se inventa.

Productores de estos markers (si cambian su formato, los round-trip tests de
`tests/plugins/chats/test_chat_events.py` truenan):
  * inbound  — `sales/translate.py::translate_to_effective_text`
  * inbound  — `sales/use_cases/ingest_inbound_message.py` (foto descrita)
  * outbound — `sales/activities/flush_ui_intents.py::_build_history_event`

DEHA: funciones puras sobre dicts. Sin I/O, sin Temporal.
"""
from __future__ import annotations

import re
from typing import Any

#: El marker lo firma el bot o, si la acción salió desde la app, el operador.
_BUTTONS_PREFIXES = ("🔘 El bot envió botones: ", "🔘 El operador envió botones: ")
_BUTTONS_BODY_SEP = " — con el mensaje: «"
_BUTTONS_TITLE_SEP = " · "

_BOT_REACTION_RE = re.compile(r"El bot reaccionó con (?P<emoji>\S+)")
_BUTTON_TAP_RE = re.compile(r"\[el cliente tocó el botón: (?P<title>[^\]]*)\]")
_REACTION_RE = re.compile(r"\[el cliente reaccionó con (?P<emoji>[^\]]*)\]")
_LEGACY_REACTION_RE = re.compile(r"\[el cliente envió un reaction\]")
_PHOTO_RE = re.compile(r"\[el cliente envió una foto: (?P<vision>[^\]]*)\]")
_RECEIPT_RE = re.compile(
    r"\[el cliente envió un comprobante de pago: (?P<vision>[^\]]*)\]"
)
_BLIND_PHOTO_RE = re.compile(r"\[el cliente envió una imagen que no pude ver bien\]")
# Flow de envío: `[datos de envío recibidos] city=…; address=…` (k=v; k=v).
_SHIPPING_MARKER = "[datos de envío recibidos]"
_SHIPPING_SPLIT_RE = re.compile(r"\s*;\s*(?=\w+=)")
_SHIPPING_FIELDS = (
    "receiver_name",
    "phone",
    "city",
    "neighborhood",
    "address",
    "payment_method",
    "order_total_cop",
    "items_summary",
)
# Plomería del Flow: no le dice nada al operador.
_SHIPPING_HIDDEN = frozenset({"flow_token"})
# El caption del cliente viaja pegado DESPUÉS del marker, entre comillas.
_CAPTION_RE = re.compile(r'con el texto: "(?P<caption>.*)"\s*\Z', re.S)


def detect_chat_event(message: dict[str, Any]) -> dict[str, Any] | None:
    """Evento estructurado de un mensaje del historial, o None.

    Tolera el banner de referral (CTWA) que el ingest antepone al marker: los
    patrones BUSCAN dentro del contenido, no anclan al inicio.
    """
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    if message.get("role") == "assistant":
        return _bot_event(message, content)
    if message.get("role") == "user":
        return _customer_event(content)
    return None


def _bot_event(message: dict[str, Any], content: str) -> dict[str, Any] | None:
    if message.get("kind") != "ui_component":
        return None
    component_kind = message.get("component_kind")

    if component_kind == "quick_replies":
        return _bot_buttons(content)

    if component_kind == "reaction":
        match = _BOT_REACTION_RE.search(content)
        return {
            "kind": "reaction",
            "emoji": match.group("emoji") if match else None,
            "author": "bot",
        }

    # El resto de componentes (catálogo, flow, galería…) sigue como nota de
    # sistema: su marker ya es una frase legible y no hay forma que recuperar.
    return None


def _bot_buttons(content: str) -> dict[str, Any] | None:
    found = next(((content.find(p), p) for p in _BUTTONS_PREFIXES if p in content), None)
    if found is None:
        return None
    start, prefix = found
    rest = content[start + len(prefix) :]

    body: str | None = None
    if _BUTTONS_BODY_SEP in rest:
        rest, body_part = rest.split(_BUTTONS_BODY_SEP, 1)
        body = body_part.removesuffix("»").strip() or None

    titles = [t.strip() for t in rest.split(_BUTTONS_TITLE_SEP) if t.strip()]
    if not titles:
        return None
    return {
        "kind": "bot_buttons",
        "body": body,
        "buttons": [{"title": t} for t in titles],
    }


def _customer_event(content: str) -> dict[str, Any] | None:
    shipping = _shipping_form(content)
    if shipping:
        return shipping

    tap = _BUTTON_TAP_RE.search(content)
    if tap:
        return {"kind": "button_tap", "title": tap.group("title").strip()}

    reaction = _REACTION_RE.search(content)
    if reaction:
        return {
            "kind": "reaction",
            "emoji": reaction.group("emoji").strip() or None,
            "author": "user",
        }
    if _LEGACY_REACTION_RE.search(content):
        # Historial viejo: la traducción tiraba el emoji. No se inventa.
        return {"kind": "reaction", "emoji": None, "author": "user"}

    return _customer_photo(content)


def _customer_photo(content: str) -> dict[str, Any] | None:
    receipt = _RECEIPT_RE.search(content)
    photo = receipt or _PHOTO_RE.search(content)
    blind = None if photo else _BLIND_PHOTO_RE.search(content)
    if not photo and not blind:
        return None

    caption_match = _CAPTION_RE.search(content)
    caption = caption_match.group("caption").strip() if caption_match else None
    return {
        "kind": "customer_photo",
        # Visión fallida: el mensaje existe, la descripción no. Mejor decirlo
        # que mostrar un bloque "Visión IA" vacío.
        "vision": photo.group("vision").strip() if photo else None,
        "caption": caption or None,
        "receipt": bool(receipt),
    }


def _shipping_form(content: str) -> dict[str, Any] | None:
    start = content.find(_SHIPPING_MARKER)
    if start < 0:
        return None
    raw = content[start + len(_SHIPPING_MARKER) :].strip()

    values: dict[str, str] = {}
    extra: list[dict[str, str]] = []
    # "(sin datos)" = el Flow llegó vacío: sin pares, la tarjeta dice "—".
    for part in _SHIPPING_SPLIT_RE.split(raw) if "=" in raw else []:
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value or key in _SHIPPING_HIDDEN:
            continue
        if key in _SHIPPING_FIELDS:
            values[key] = value
        else:
            extra.append({"key": key, "value": value})

    event: dict[str, Any] = {"kind": "shipping_form"}
    for field in _SHIPPING_FIELDS:
        event[field] = values.get(field)
    total = values.get("order_total_cop")
    event["order_total_cop"] = int(total) if total and total.isdigit() else None
    event["extra"] = extra
    return event


def annotate_touched_buttons(messages: list[dict[str, Any]]) -> None:
    """Marca `touched: True` en el botón que el cliente tocó, DENTRO del
    mensaje de botones al que pertenece. Muta los eventos in place.

    Correlación posicional: el tap pertenece a la última tanda de botones
    enviada antes de él. WhatsApp manda el `context` con el id del mensaje
    original, pero el marker del bot no persiste su wamid — mientras no lo
    haga, "la tanda más reciente" es la respuesta correcta en la práctica (es
    la única que el cliente tiene a mano en su chat).
    """
    pending: list[dict[str, Any]] | None = None
    for message in messages:
        event = message.get("event")
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "bot_buttons":
            pending = event.get("buttons")
        elif event.get("kind") == "button_tap" and pending:
            title = event.get("title")
            for button in pending:
                if button.get("title") == title:
                    button["touched"] = True
                    break
