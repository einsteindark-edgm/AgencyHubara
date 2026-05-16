"""Parser puro del payload entrante de WhatsApp Cloud API (Meta).

Distingue tres casos:

* mensaje real (text o media): retorna `WhatsAppMessage`.
* status update / heartbeats / cualquier evento valido sin mensaje: retorna `None`.
* shape totalmente desconocida (no es un payload de Meta): lanza `ValueError`.

La distincion es importante porque el handler HTTP debe responder 400 ante un body
malformed (alguien atacando el webhook) pero 200 ante un status update legitimo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WhatsAppMessage:
    message_id: str
    from_number: str
    phone_number_id: str
    text: str | None
    media: dict | None
    timestamp: str


def parse_whatsapp_inbound(body: dict) -> WhatsAppMessage | None:
    """Parsea un webhook de WhatsApp.

    Retorna `None` si el payload es valido pero no contiene un mensaje (p.ej. un
    status update). Lanza `ValueError` si la shape no es la esperada.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a dict")

    entries = body.get("entry")
    if not isinstance(entries, list) or not entries:
        raise ValueError("missing or empty 'entry' array")

    entry = entries[0]
    if not isinstance(entry, dict):
        raise ValueError("entry[0] must be a dict")

    changes = entry.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("missing or empty 'changes' array")

    change = changes[0]
    if not isinstance(change, dict):
        raise ValueError("changes[0] must be a dict")

    value = change.get("value")
    if not isinstance(value, dict):
        raise ValueError("missing 'value' object")

    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("missing 'metadata' object")

    phone_number_id = metadata.get("phone_number_id")
    if not isinstance(phone_number_id, str):
        raise ValueError("missing 'phone_number_id'")

    messages = value.get("messages")
    if not messages:
        # Status updates, statuses array, or empty messages: not an inbound message.
        return None
    if not isinstance(messages, list):
        raise ValueError("'messages' must be a list")

    msg = messages[0]
    if not isinstance(msg, dict):
        raise ValueError("messages[0] must be a dict")

    message_id = msg.get("id")
    from_number = msg.get("from")
    timestamp = msg.get("timestamp")
    msg_type = msg.get("type")

    if not isinstance(message_id, str):
        raise ValueError("missing 'id' on message")
    if not isinstance(from_number, str):
        raise ValueError("missing 'from' on message")
    if not isinstance(timestamp, str):
        raise ValueError("missing 'timestamp' on message")
    if not isinstance(msg_type, str):
        raise ValueError("missing 'type' on message")

    text: str | None = None
    media: dict | None = None

    if msg_type == "text":
        text_obj = msg.get("text")
        if not isinstance(text_obj, dict) or not isinstance(text_obj.get("body"), str):
            raise ValueError("text message missing 'text.body'")
        text = text_obj["body"]
    else:
        # media or interactive payloads: pass through the inner object so callers
        # that eventually support media have it; none of them are processed today.
        media_obj = msg.get(msg_type)
        if isinstance(media_obj, dict):
            media = {"type": msg_type, **media_obj}
        else:
            # Unknown subtype but still has an `id`/`from`/`timestamp`: not fatal,
            # but caller cannot do anything with it. Treat as non-message.
            return None

    return WhatsAppMessage(
        message_id=message_id,
        from_number=from_number,
        phone_number_id=phone_number_id,
        text=text,
        media=media,
        timestamp=timestamp,
    )
