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

import re
from dataclasses import dataclass, field
from typing import Any

# SEC-12: `from_number` de Meta es un teléfono E.164 (solo dígitos, sin `+`).
# Se convierte en `session_id = wa_<from>` que llega al filesystem del vault, así
# que exigimos este shape para bloquear path traversal / injection en el id.
_PHONE_RE = re.compile(r"^\d{6,15}$")


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

    return _parse_message(messages[0], phone_number_id)


def _parse_message(msg: Any, phone_number_id: str) -> WhatsAppMessage | None:
    """Un ítem de ``messages[]`` → ``WhatsAppMessage`` (``None`` si no es un
    mensaje aprovechable; ``ValueError`` si la shape es inválida). Compartido
    por el inbound regular y por ``standby.messages`` (mismo esquema)."""
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
    # SEC-12: `from_number` se convierte en `session_id = wa_<from>` que llega al
    # filesystem del vault (metadata + media). Exigir un teléfono E.164 (solo
    # dígitos, 6-15) evita path traversal (`../`) y command-injection en el id.
    if not _PHONE_RE.match(from_number):
        raise ValueError("'from' no es un número de teléfono válido (E.164)")
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


@dataclass(frozen=True)
class WhatsAppStatusUpdate:
    """Status update normalizado de ``entry[*].changes[*].value.statuses[]``.

    Meta envía uno o más statuses por webhook delivery (sent / delivered /
    read / failed). El `pricing` puede faltar (e.g. ``status=failed`` sin
    delivery exitoso), por eso es ``dict | None``.

    HU-WA24H-001 F1.10 — ver `IngestDeliveryStatus` use case.
    """

    wa_message_id: str
    status: str  # "sent" | "delivered" | "read" | "failed"
    pricing: dict[str, Any] | None  # {billable, pricing_type, category} | None


def parse_whatsapp_statuses(body: dict) -> list[WhatsAppStatusUpdate]:
    """Extrae el array `statuses[]` del webhook Meta.

    Defensivo contra shapes malformadas: si el body no tiene la estructura
    esperada devuelve ``[]`` (NO levanta — los inbound messages se siguen
    parseando aparte). Solo levanta ``ValueError`` para shapes obviamente
    inválidas que `parse_whatsapp_inbound` también rechaza.

    Cada status del array tiene shape Meta:
        {"id": "wamid.HBg...", "status": "delivered",
         "timestamp": "...", "recipient_id": "+57...",
         "pricing": {"billable": true, "pricing_type": "regular",
                     "category": "marketing"}}

    Retorna lista vacía si no hay ``statuses[]`` o todos son malformados.
    """
    if not isinstance(body, dict):
        return []
    entries = body.get("entry")
    if not isinstance(entries, list):
        return []

    out: list[WhatsAppStatusUpdate] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            statuses = value.get("statuses")
            if not isinstance(statuses, list):
                continue
            for status in statuses:
                parsed = _parse_status(status)
                if parsed is not None:
                    out.append(parsed)
    return out


def _parse_status(status: Any) -> WhatsAppStatusUpdate | None:
    if not isinstance(status, dict):
        return None
    wa_message_id = status.get("id")
    status_kind = status.get("status")
    if not isinstance(wa_message_id, str) or not isinstance(status_kind, str):
        return None
    pricing = status.get("pricing")
    # pricing puede ser dict o ausente; ambos son válidos
    if pricing is not None and not isinstance(pricing, dict):
        pricing = None
    return WhatsAppStatusUpdate(wa_message_id=wa_message_id, status=status_kind, pricing=pricing)


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


# =============================================================================
# Webhook `standby` (D1.4 MBA): el oído cuando Meta Business Agent controla
# =============================================================================
#
# Referencia Meta "Standby webhooks" (2026-08-04): sobre común con
# ``entry[].changes[].field == "standby"`` y ``value.standby`` con UNO de
# ``messages`` (+``contacts``), ``message_echoes`` o ``statuses``. Los mensajes
# usan el mismo esquema que el inbound regular; los ecos traen el body EXACTO
# que MBA pasó a ``POST /{phone-number-id}/messages`` (no el texto renderizado);
# los statuses traen ``pricing`` con la clave ``type`` (no ``pricing_type``).

STANDBY_FIELD = "standby"
HANDOVERS_FIELD = "messaging_handovers"
#: Un `wamid.*` real ronda los 60-90 chars; el id entra al vault (dedupe), así
#: que un router público no acepta ids de longitud arbitraria.
MAX_WAMID_LEN = 128


def webhook_fields(body: Any) -> set[str]:
    """Los ``entry[*].changes[*].field`` presentes (vacío si no vienen)."""
    out: set[str] = set()
    if not isinstance(body, dict) or not isinstance(body.get("entry"), list):
        return out
    for entry in body["entry"]:
        for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
            if isinstance(change, dict) and isinstance(change.get("field"), str):
                out.add(change["field"])
    return out


def split_webhook_by_field(body: Any) -> dict[str, dict[str, Any]]:
    """Reparte los ``changes`` del body por ``field`` en sub-bodies con el
    mismo sobre (``object``, ``entry[].id``): cada handler ve SOLO sus cambios.
    Un change sin ``field`` (payloads simulados / legacy) cuenta como
    ``messages``. Vacío si el body no tiene entries."""
    if not isinstance(body, dict) or not isinstance(body.get("entry"), list):
        return {}
    parts: dict[str, dict[str, Any]] = {}
    for entry in body["entry"]:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field_name = change.get("field") if isinstance(change.get("field"), str) else "messages"
            part = parts.setdefault(field_name, {k: v for k, v in body.items() if k != "entry"} | {"entry": []})
            entries: list[dict[str, Any]] = part["entry"]
            if not entries or entries[-1].get("id") != entry.get("id"):
                entries.append({k: v for k, v in entry.items() if k != "changes"} | {"changes": []})
            entries[-1]["changes"].append(change)
    return parts


@dataclass(frozen=True)
class StandbyEcho:
    """Copia de un mensaje que MBA (u otro remitente del número) envió."""

    wamid: str
    to: str  # WA ID del cliente (dígitos) → sesión ``wa_<to>``
    timestamp: str  # epoch segundos, como lo manda Meta
    msg_type: str  # text | template | interactive | image | ...
    text: str | None  # cuerpo textual si lo hay (text.body, caption, interactive.body)
    template_name: str | None
    message: dict[str, Any] = field(default_factory=dict)  # el body exacto enviado


@dataclass(frozen=True)
class StandbyEvent:
    phone_number_id: str
    messages: tuple[WhatsAppMessage, ...] = ()
    echoes: tuple[StandbyEcho, ...] = ()
    statuses: tuple[WhatsAppStatusUpdate, ...] = ()


def parse_whatsapp_standby(body: Any) -> StandbyEvent | None:
    """Todos los cambios ``standby`` del body (Meta puede agrupar varios).
    ``None`` si el body no trae ninguno. Defensivo: un ítem malformado se
    descarta, nunca levanta (el resto del webhook sigue)."""
    if not isinstance(body, dict) or not isinstance(body.get("entry"), list):
        return None
    found = False
    phone_number_id = ""
    messages: list[WhatsAppMessage] = []
    echoes: list[StandbyEcho] = []
    statuses: list[WhatsAppStatusUpdate] = []
    for entry in body["entry"]:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            standby = value.get("standby")
            if change.get("field") != STANDBY_FIELD and not isinstance(standby, dict):
                continue
            found = True
            if not isinstance(standby, dict):
                continue
            meta = value.get("metadata")
            pnid = meta.get("phone_number_id") if isinstance(meta, dict) else None
            if isinstance(pnid, str) and pnid:
                phone_number_id = phone_number_id or pnid
            for raw in standby.get("messages") or []:
                try:
                    parsed = _parse_message(raw, phone_number_id)
                except ValueError:
                    continue
                if parsed is not None and len(parsed.message_id) <= MAX_WAMID_LEN:
                    messages.append(parsed)
            for raw in standby.get("message_echoes") or []:
                echo = _parse_echo(raw)
                if echo is not None:
                    echoes.append(echo)
            for raw in standby.get("statuses") or []:
                st = _parse_status(raw)
                if st is not None:
                    statuses.append(st)
    if not found:
        return None
    return StandbyEvent(
        phone_number_id=phone_number_id, messages=tuple(messages), echoes=tuple(echoes), statuses=tuple(statuses)
    )


def _parse_echo(raw: Any) -> StandbyEcho | None:
    if not isinstance(raw, dict):
        return None
    wamid = raw.get("id")
    message = raw.get("message")
    if not isinstance(wamid, str) or not 0 < len(wamid) <= MAX_WAMID_LEN or not isinstance(message, dict):
        return None
    to = message.get("to")
    # SEC-12: ``to`` se vuelve ``session_id = wa_<to>`` en el filesystem del vault.
    if not isinstance(to, str) or not _PHONE_RE.match(to):
        return None
    msg_type = message.get("type") if isinstance(message.get("type"), str) else "unknown"
    text: str | None = None
    template_name: str | None = None
    if msg_type == "text":
        body = message.get("text")
        text = body.get("body") if isinstance(body, dict) and isinstance(body.get("body"), str) else None
    elif msg_type == "template":
        tpl = message.get("template")
        sibling = raw.get("template")
        for candidate in (tpl, sibling):
            if isinstance(candidate, dict) and isinstance(candidate.get("name"), str):
                template_name = candidate["name"]
                break
    elif msg_type == "interactive":
        inter = message.get("interactive")
        body = inter.get("body") if isinstance(inter, dict) else None
        text = body.get("text") if isinstance(body, dict) and isinstance(body.get("text"), str) else None
    else:
        inner = message.get(msg_type)
        caption = inner.get("caption") if isinstance(inner, dict) else None
        text = caption if isinstance(caption, str) else None
    timestamp = raw.get("timestamp")
    return StandbyEcho(
        wamid=wamid,
        to=to,
        timestamp=timestamp if isinstance(timestamp, str) else "",
        msg_type=msg_type,
        text=text,
        template_name=template_name,
        message=message,
    )


_MEDIA_LABELS_ES = {"image": "imagen", "video": "video", "document": "documento", "sticker": "sticker", "audio": "audio"}


def echo_display_text(echo: StandbyEcho) -> str:
    """Texto legible para el historial de la sesión (dashboard + evals)."""
    if echo.msg_type == "text":
        return echo.text or ""
    if echo.msg_type == "template":
        return f"[plantilla {echo.template_name or 'sin nombre'}]"
    if echo.msg_type == "interactive":
        inter = echo.message.get("interactive")
        sub = inter.get("type") if isinstance(inter, dict) and isinstance(inter.get("type"), str) else "interactive"
        return f"[{sub}] {echo.text or ''}".strip()
    label = _MEDIA_LABELS_ES.get(echo.msg_type, echo.msg_type)
    return f"[{label}] {echo.text or ''}".strip()


def inbound_display_text(msg: WhatsAppMessage) -> str:
    """Texto legible de un inbound (standby no corre visión ni transcripción)."""
    if msg.text is not None:
        return msg.text
    if msg.interactive:
        kind = msg.interactive.get("type")
        if kind in ("button_reply", "list_reply"):
            return str(msg.interactive.get("title") or "")
        if kind == "nfm_reply":
            return f"[formulario] {msg.interactive.get('body') or ''}".strip()
        return f"[{kind or 'interactive'}]"
    if msg.location:
        name = msg.location.get("name") or msg.location.get("address") or ""
        coords = f"{msg.location.get('latitude')},{msg.location.get('longitude')}"
        return f"[ubicación] {name or coords}"
    if msg.audio:
        return "[audio]"
    if msg.order:
        return f"[carrito] {msg.order.get('text') or ''}".strip()
    if msg.contacts:
        return "[contacto compartido]"
    if msg.media:
        label = _MEDIA_LABELS_ES.get(str(msg.media.get("type")), str(msg.media.get("type")))
        caption = msg.media.get("caption") or msg.media.get("filename") or ""
        return f"[{label}] {caption}".strip()
    return f"[{msg.msg_type}]"


# =============================================================================
# Webhook `messaging_handovers` (D1.5 MBA): quién controla el hilo
# =============================================================================
#
# Meta lo dispara cada vez que el control del hilo cambia entre Business Agent
# y nuestra app (al enviar un mensaje desde Hubara → lo tomamos; tras
# `thread_control release` → vuelve a MBA). La referencia de WhatsApp NO está
# publicada todavía (2026-09-08: la URL redirige al home); el parser acepta el
# shape del roadmap (`control_taken` con `previous_owner_app_id` /
# `new_owner_app_id` / `metadata`) y el del protocolo de traspaso de Messenger
# (`pass_thread_control` / `take_thread_control`, ids como str o int,
# timestamp en s o ms), con el cliente en `sender.id` (o `recipient_id` /
# `wa_id` / `from` / `to`). Un ítem que no se entiende cuenta en `unparsed`
# (el handler lo loguea con el body para verificar el shape en F0), nunca
# levanta.

_HANDOVER_CONTROL_KEYS = ("control_taken", "pass_thread_control", "take_thread_control")
_HANDOVER_CUSTOMER_KEYS = ("recipient_id", "wa_id", "from", "to", "customer_phone")


@dataclass(frozen=True)
class HandoverEvent:
    customer: str  # WA ID del cliente (dígitos) → sesión ``wa_<customer>``
    kind: str  # control_taken | pass_thread_control | take_thread_control | <event>
    new_owner_app_id: str | None
    previous_owner_app_id: str | None
    timestamp_ms: int | None
    metadata: str | None


@dataclass(frozen=True)
class HandoversEvent:
    phone_number_id: str
    handovers: tuple[HandoverEvent, ...] = ()
    unparsed: int = 0  # ítems con shape desconocido / cliente inválido


def parse_messaging_handovers(body: Any) -> HandoversEvent | None:
    """Todos los cambios ``messaging_handovers`` del body. ``None`` si no trae
    ninguno. Defensivo: nunca levanta."""
    if not isinstance(body, dict) or not isinstance(body.get("entry"), list):
        return None
    found = False
    phone_number_id = ""
    handovers: list[HandoverEvent] = []
    unparsed = 0
    for entry in body["entry"]:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            items = value.get("messaging_handovers")
            if change.get("field") != HANDOVERS_FIELD and not isinstance(items, list):
                continue
            found = True
            meta = value.get("metadata")
            pnid = meta.get("phone_number_id") if isinstance(meta, dict) else None
            if isinstance(pnid, str) and pnid:
                phone_number_id = phone_number_id or pnid
            if not isinstance(items, list):
                # sin lista: quizá el ítem viene plano en `value`
                items = [value] if any(k in value for k in _HANDOVER_CONTROL_KEYS) else []
                if not items:
                    unparsed += 1
            for raw in items:
                parsed = _parse_handover(raw)
                if parsed is None:
                    unparsed += 1
                else:
                    handovers.append(parsed)
    if not found:
        return None
    return HandoversEvent(phone_number_id=phone_number_id, handovers=tuple(handovers), unparsed=unparsed)


def _app_id(raw: Any) -> str | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        return raw.strip() or None
    return None


def _timestamp_ms(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int) or raw <= 0:
        return None
    # WhatsApp manda epoch en segundos (str); Messenger en milisegundos (int).
    return raw if raw >= 10**12 else raw * 1000


def _handover_customer(raw: dict[str, Any]) -> str | None:
    sender = raw.get("sender")
    candidates = [sender.get("id") if isinstance(sender, dict) else None]
    candidates += [raw.get(k) for k in _HANDOVER_CUSTOMER_KEYS]
    for c in candidates:
        # SEC-12: se vuelve ``session_id = wa_<c>`` en el filesystem del vault.
        if isinstance(c, str) and _PHONE_RE.match(c):
            return c
    return None


def _parse_handover(raw: Any) -> HandoverEvent | None:
    if not isinstance(raw, dict):
        return None
    customer = _handover_customer(raw)
    if customer is None:
        return None
    kind: str | None = None
    control: dict[str, Any] | None = None
    for key in _HANDOVER_CONTROL_KEYS:
        if isinstance(raw.get(key), dict):
            kind, control = key, raw[key]
            break
    if control is None:
        if "new_owner_app_id" not in raw and "previous_owner_app_id" not in raw:
            return None
        control = raw
        event = raw.get("event")
        kind = event if isinstance(event, str) and event else "unknown"
    metadata = control.get("metadata")
    return HandoverEvent(
        customer=customer,
        kind=kind or "unknown",
        new_owner_app_id=_app_id(control.get("new_owner_app_id")),
        previous_owner_app_id=_app_id(control.get("previous_owner_app_id")),
        timestamp_ms=_timestamp_ms(raw.get("timestamp", control.get("timestamp"))),
        metadata=metadata if isinstance(metadata, str) else None,
    )
