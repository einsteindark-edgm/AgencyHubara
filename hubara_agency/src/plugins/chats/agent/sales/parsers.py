"""Parser puro del payload entrante de WhatsApp Cloud API (Meta).

Distingue cuatro casos:

* mensaje real (text, media, interactive, location, audio, order, etc.):
  retorna `WhatsAppMessage`.
* status update / heartbeats: retorna `None`.
* referral solo (sin mensaje): improbable pero posible — actualmente lo
  tratamos como None y dejamos al ingestor confiar en messages[].
* shape totalmente desconocida (no es un payload de Meta): lanza `ValueError`.

`WhatsAppMessage` es extendida (vs versión legacy text/media):
  - `text`: mensaje de texto plano del cliente
  - `media`: media subido inbound (image/video/document/sticker) sin
    procesamiento — el ingestor decide qué hacer
  - `interactive`: respuesta a un componente UI nuestro (button, list,
    flow nfm_reply, order)
  - `location`: cliente compartió ubicación nativa
  - `audio`: audio inbound — el ingestor encola transcripción
  - `referral`: atribución CTWA / FB post (puede venir en CUALQUIER tipo)
  - `context`: el cliente respondió citando un mensaje específico nuestro
  - `pricing` etc se podrían sumar después

R-JSON: la dataclass es frozen + composable. Cada subfield es un dict
crudo para no encerrar al ingestor en estructuras tipadas que serían
duplicadas de las DTOs ya definidas en `src/platform/whatsapp/dtos.py`
(las DTOs tipadas se usan en la *capa de orquestación*; el parser se
queda como bridge mínimo del payload Meta hacia el ingestor).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WhatsAppMessage:
    """Mensaje inbound normalizado de WhatsApp Cloud API.

    Solo UNO de {text, media, interactive, location, audio, order} suele
    estar presente. Los demás son None. `referral` y `context` pueden
    acompañar a cualquiera.

    NOTA orden de campos: los seis primeros (message_id, from_number,
    phone_number_id, text, media, timestamp) preservan el contrato legacy
    para no romper consumers existentes (`test_ingest_inbound_message.py`,
    código de prod del ingest use case). Los nuevos campos van DESPUÉS con
    defaults.
    """

    # --- legacy fields (mantenidos en este orden para backward-compat) ---
    message_id: str
    from_number: str
    phone_number_id: str
    text: str | None
    media: dict[str, Any] | None
    timestamp: str

    # --- nuevos campos, todos con default ---
    msg_type: str = "text"  # raw Meta type ("text" | "image" | "interactive" | ...)
    interactive: dict[str, Any] | None = None  # button_reply | list_reply | nfm_reply
    location: dict[str, Any] | None = None  # {latitude, longitude, name?, address?}
    audio: dict[str, Any] | None = None  # {id, mime_type, voice: bool}
    order: dict[str, Any] | None = None  # `type: "order"` — carrito WA
    referral: dict[str, Any] | None = None  # CTWA / FB post attribution
    context: dict[str, Any] | None = None  # quoted message
    contacts: list[dict[str, Any]] | None = None  # cliente compartió contacto
    raw: dict[str, Any] = field(default_factory=dict)  # mensaje completo para edge cases


def parse_whatsapp_inbound(body: dict) -> WhatsAppMessage | None:
    """Parsea un webhook de WhatsApp Cloud API.

    Retorna `None` si el payload es válido pero no contiene un mensaje
    (ej: status update). Lanza `ValueError` si la shape no es esperada.
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
        # Status updates u otros eventos sin messages[]: not an inbound message.
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
    media: dict[str, Any] | None = None
    interactive: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    contacts: list[dict[str, Any]] | None = None

    # --- text ---
    if msg_type == "text":
        text_obj = msg.get("text")
        if not isinstance(text_obj, dict) or not isinstance(text_obj.get("body"), str):
            raise ValueError("text message missing 'text.body'")
        text = text_obj["body"]

    # --- interactive (button_reply / list_reply / nfm_reply) ---
    elif msg_type == "interactive":
        interactive_obj = msg.get("interactive")
        if not isinstance(interactive_obj, dict):
            raise ValueError("interactive message missing 'interactive' object")
        sub_type = interactive_obj.get("type")
        if sub_type == "button_reply":
            reply = interactive_obj.get("button_reply") or {}
            interactive = {
                "type": "button_reply",
                "id": reply.get("id", ""),
                "title": reply.get("title", ""),
            }
        elif sub_type == "list_reply":
            reply = interactive_obj.get("list_reply") or {}
            interactive = {
                "type": "list_reply",
                "id": reply.get("id", ""),
                "title": reply.get("title", ""),
                "description": reply.get("description"),
            }
        elif sub_type == "nfm_reply":
            reply = interactive_obj.get("nfm_reply") or {}
            # response_json viene como string JSON-encoded
            raw_resp = reply.get("response_json", "{}")
            try:
                import json
                parsed_resp = json.loads(raw_resp) if isinstance(raw_resp, str) else raw_resp
            except (ValueError, TypeError):
                parsed_resp = {"_raw": raw_resp}
            interactive = {
                "type": "nfm_reply",
                "name": reply.get("name"),
                "body": reply.get("body"),
                "response_json": parsed_resp,
            }
        else:
            interactive = {"type": sub_type, "raw": interactive_obj}

    # --- location ---
    elif msg_type == "location":
        loc_obj = msg.get("location")
        if not isinstance(loc_obj, dict):
            raise ValueError("location message missing 'location' object")
        location = {
            "latitude": float(loc_obj.get("latitude", 0.0)),
            "longitude": float(loc_obj.get("longitude", 0.0)),
            "name": loc_obj.get("name"),
            "address": loc_obj.get("address"),
        }

    # --- audio (voice notes y audios) ---
    elif msg_type == "audio":
        audio_obj = msg.get("audio")
        if not isinstance(audio_obj, dict):
            raise ValueError("audio message missing 'audio' object")
        audio = {
            "id": audio_obj.get("id"),
            "mime_type": audio_obj.get("mime_type"),
            "sha256": audio_obj.get("sha256"),
            "voice": bool(audio_obj.get("voice", False)),
        }

    # --- order (cart submit del cliente desde catalog browsing) ---
    elif msg_type == "order":
        order_obj = msg.get("order")
        if not isinstance(order_obj, dict):
            raise ValueError("order message missing 'order' object")
        order = {
            "catalog_id": order_obj.get("catalog_id"),
            "text": order_obj.get("text"),
            "product_items": list(order_obj.get("product_items") or []),
        }

    # --- contacts (cliente compartió vCard) ---
    elif msg_type == "contacts":
        contacts_raw = msg.get("contacts")
        if isinstance(contacts_raw, list):
            contacts = contacts_raw

    # --- media (image / video / document / sticker) ---
    elif msg_type in {"image", "video", "document", "sticker"}:
        media_obj = msg.get(msg_type)
        if isinstance(media_obj, dict):
            media = {"type": msg_type, **media_obj}
        else:
            # Unknown shape but type es válido: dejamos None y ingestor decide
            media = None

    # --- catch-all ---
    else:
        # Tipo desconocido. Dejamos pasar como raw para que el ingestor lo
        # loguee y eventualmente lo soporte. NO raisea para no perder
        # mensajes legítimos cuando Meta agrega nuevos tipos.
        inner = msg.get(msg_type)
        if isinstance(inner, dict):
            media = {"type": msg_type, **inner}
        else:
            # No hay payload aprovechable: tratamos como non-message.
            return None

    # --- referral (puede acompañar a cualquier tipo) ---
    referral = _parse_referral(msg.get("referral"))

    # --- context (quoted message) ---
    context = _parse_context(msg.get("context"))

    return WhatsAppMessage(
        message_id=message_id,
        from_number=from_number,
        phone_number_id=phone_number_id,
        text=text,
        media=media,
        timestamp=timestamp,
        msg_type=msg_type,
        interactive=interactive,
        location=location,
        audio=audio,
        order=order,
        referral=referral,
        context=context,
        contacts=contacts,
        raw=msg,
    )


def _parse_referral(obj: Any) -> dict[str, Any] | None:
    """Extrae el objeto referral completo de un inbound CTWA / FB post.

    Campos (todos opcionales — Meta los omite si no aplican):

    * `source_url` — URL del ad/post de Meta
    * `source_id` — id Meta interno del ad/post
    * `source_type` — "ad" | "post"
    * `headline` — título del creative
    * `body` — descripción
    * `media_type` — "image" | "video"
    * `image_url`, `video_url`, `thumbnail_url` — assets crudos
    * `ctwa_clid` — Click ID (solo Android/iOS, ausente en WhatsApp Web)
    * `referred_product` — `{catalog_id, product_retailer_id}` si el ad
      mostraba producto específico

    Se persiste íntegro para attribution via Conversions API.
    """
    if not isinstance(obj, dict):
        return None
    keep = (
        "source_url",
        "source_id",
        "source_type",
        "headline",
        "body",
        "media_type",
        "image_url",
        "video_url",
        "thumbnail_url",
        "ctwa_clid",
        "referred_product",
    )
    out: dict[str, Any] = {}
    for k in keep:
        if k in obj and obj[k] is not None:
            out[k] = obj[k]
    # Si no hay nada útil, devolvemos None
    return out or None


def _parse_context(obj: Any) -> dict[str, Any] | None:
    """Extrae el objeto context (quoted message) de un inbound.

    * `from` — número del autor del msg citado (nuestro phone_number_id si
      el cliente cita algo que nosotros mandamos)
    * `id` — message_id del msg citado
    * `forwarded` — True si el cliente reenvió
    * `referred_product` — si el cliente cita un product card y el msg
      original era catalog (catalog_id + product_retailer_id)
    """
    if not isinstance(obj, dict):
        return None
    keep = ("from", "id", "forwarded", "frequently_forwarded", "referred_product")
    out = {k: obj[k] for k in keep if k in obj}
    return out or None
