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

**La excepción, `card`** (2026-10-13, run 2395ffd6): la línea del historial
recorta los textos largos (Jev la lee) y no lista los productos, así que el
saludo que el bot escribió en el texto del catálogo no se veía en el panel.
El flush guarda aparte el mensaje entero que leyó el cliente y lo que traía
(`card`); acá se valida y sale como `bot_card` (o completa el cuerpo de
`bot_buttons`). El historial anterior no la tiene y sigue como nota.

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
    card = _card(message.get("card"))

    if component_kind == "quick_replies":
        buttons = _bot_buttons(content)
        if buttons is not None and card and card["text"]:
            # La línea recorta el cuerpo; la tarjeta lo trae entero.
            buttons["body"] = card["text"]
        return buttons

    if component_kind == "reaction":
        match = _BOT_REACTION_RE.search(content)
        return {
            "kind": "reaction",
            "emoji": match.group("emoji") if match else None,
            "author": "bot",
        }

    if card:
        label, icon = _CARD_LOOK.get(str(component_kind), _CARD_LOOK_DEFAULT)
        return {
            "kind": "bot_card",
            "component": str(component_kind or ""),
            "label": label,
            "icon": icon,
            **card,
        }

    # Sin tarjeta (historial viejo, flow, pedido…) sigue como nota de sistema:
    # su marker ya es una frase legible y no hay forma que recuperar.
    return None


#: Qué fue cada envío (en palabras del operador) y su ícono: los decide el
#: servidor para que la app y el panel pinten la tarjeta sin conocer el
#: componente — uno nuevo se ve bien sin publicar otra versión de la app. El
#: ícono es una llave de una lista cerrada que la app y el panel traducen.
_CARD_LOOK: dict[str, tuple[str, str]] = {
    "products_list": ("Catálogo", "catalog"),
    "categories": ("Menú de categorías", "list"),
    "cta_url": ("Botón con enlace", "link"),
    "product_detail": ("Foto del producto", "photo"),
    "product_gallery": ("Fotos del producto", "photo"),
    "shipping_flow": ("Formulario de envío", "form"),
    "order_confirmation": ("Resumen para confirmar", "order"),
    "contact_card": ("Tarjeta de contacto", "contact"),
}
_CARD_LOOK_DEFAULT = ("Mensaje interactivo", "message")


def _str(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _card(raw: Any) -> dict[str, Any] | None:
    """La tarjeta que el flush guardó con el mensaje (`card`), validada: lo
    mal formado se descarta y, si no queda nada que mostrar, None."""
    if not isinstance(raw, dict):
        return None
    items = [
        {"title": title, "detail": _str(item.get("detail"))}
        for item in raw.get("items") or []
        if isinstance(item, dict) and (title := _str(item.get("title")))
    ] if isinstance(raw.get("items"), list) else []
    images = [
        {"url": url, "caption": _str(image.get("caption"))}
        for image in raw.get("images") or []
        if isinstance(image, dict) and (url := _str(image.get("url")))
    ] if isinstance(raw.get("images"), list) else []
    buttons = [
        {"title": title}
        for button in raw.get("buttons") or []
        if isinstance(button, dict) and (title := _str(button.get("title")))
    ] if isinstance(raw.get("buttons"), list) else []
    link_raw = raw.get("link")
    link_url = _str(link_raw.get("url")) if isinstance(link_raw, dict) else None
    link = {"label": _str(link_raw.get("label")) or link_url, "url": link_url} if link_url else None
    text = _str(raw.get("text"))
    if not (text or items or images or link or buttons):
        return None
    return {
        "title": _str(raw.get("title")),
        "text": text,
        "items": items,
        "buttons": buttons,
        "action": _str(raw.get("action")),
        "link": link,
        "images": images,
    }


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
        if event.get("kind") == "bot_buttons" or (
            event.get("kind") == "bot_card" and event.get("buttons")
        ):
            # Los botones de respuesta y los del resumen del pedido.
            pending = event.get("buttons")
        elif event.get("kind") == "button_tap" and pending:
            title = event.get("title")
            for button in pending:
                if button.get("title") == title:
                    button["touched"] = True
                    break
