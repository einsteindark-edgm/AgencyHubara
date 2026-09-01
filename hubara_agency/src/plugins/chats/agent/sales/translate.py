"""Traducción de mensajes inbound interactivos a "texto efectivo" para el LLM.

Motivación: el LLM razona en lenguaje natural. Si recibe
`{"interactive": {"type": "button_reply", "id": "ver_catalogo"}}` como JSON
cruzando el system prompt, rompe su modelo mental de la conversación —
empieza a hablar de "el ID que tocaste" en vez de simplemente "ver el
catálogo". La traducción acá inyecta el equivalente natural en español
neutro Hubara.

Tabla de traducción:

| Tipo inbound       | Texto efectivo                                              |
|--------------------|-------------------------------------------------------------|
| `text`             | (passthrough)                                               |
| `interactive.button_reply`  | `[el cliente tocó el botón: <title>]`              |
| `interactive.list_reply`    | `[el cliente seleccionó: <title del producto>]`    |
| `interactive.nfm_reply` (Flow) | `[datos de envío recibidos] ciudad=...; barrio=...` |
| `location`         | `[el cliente compartió su ubicación] lat=X lng=Y (ciudad)`  |
| `audio`            | `<texto transcrito>` (post-transcripción, ver A.5)          |
| `order` (cart submit) | `[el cliente armó un carrito con: 2× cruz-de-vida, 1× ...]` |
| `image/video/document/sticker` | `[el cliente envió un <tipo>]`                  |
| `contacts`         | `[el cliente compartió un contacto: <nombre>]`              |

Si el inbound viene con `referral` (CTWA / FB post), se le antepone un
"banner" al texto efectivo para que el LLM sepa que es la primera
interacción originada en un anuncio. El banner se inyecta UNA SOLA VEZ:
si ya estaba registrado el ctwa_clid en state, se omite (ver A.0.3 + A.7).

DEHA: módulo puro, sin Temporal. Lee opcionalmente del `CatalogPort` para
resolver `handle → title` en list_reply. Si el port no está disponible,
hace fallback "el cliente seleccionó: <id>" (degradado pero no roto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage

if TYPE_CHECKING:
    from src.platform.catalog import CatalogPort


@dataclass(frozen=True)
class EffectiveText:
    """Resultado de la traducción del inbound a texto LLM-ready.

    * `text`: el texto natural que verá el LLM. None si el mensaje no
      aporta semántica para procesar (ej: status update inesperado).
    * `requires_transcription`: True si es audio que aún no fue transcrito.
      El ingestor encola `transcribe_audio_activity` y, cuando vuelve,
      reinyecta un mensaje text sintético usando ese resultado.
    * `audio_media_id`: id del media en Meta para descarga (solo si
      requires_transcription).
    * `referral_attribution`: si el inbound trajo referral, se persiste
      acá el dict completo para que el ingest lo guarde en state.json y
      pase a la Conversions API.
    * `structured_payload`: payload tipado del Flow submit u order — el
      ingest puede usarlo aparte para decisiones de orquestación sin
      reparsear el texto.
    * `is_first_touch_from_ad`: True si es la primera interacción de
      esta sesión via CTWA. El ingest se encarga del banner.
    """

    text: str | None
    requires_transcription: bool = False
    audio_media_id: str | None = None
    requires_vision: bool = False
    image_media_id: str | None = None
    image_mime_type: str | None = None
    referral_attribution: dict[str, Any] | None = None
    structured_payload: dict[str, Any] | None = None
    is_first_touch_from_ad: bool = False
    media_inbound_kind: str | None = None  # "image"|"video"|"document"|"sticker"|"audio"|None
    quoted_message_id: str | None = None
    # Documento PDF inbound (comprobante de pago típico): el ingest descarga y
    # persiste los bytes para el dashboard — sin visión ni reentry. Solo se
    # poblan para `type=document` con mime `application/pdf`.
    document_media_id: str | None = None
    document_mime_type: str | None = None
    document_filename: str | None = None
    debug_tags: list[str] = field(default_factory=list)


async def translate_to_effective_text(
    msg: WhatsAppMessage,
    *,
    catalog: "CatalogPort | None" = None,
    referral_already_seen: bool = False,
) -> EffectiveText:
    """Traduce un `WhatsAppMessage` a texto natural LLM-ready.

    Args:
      msg: el mensaje parseado del webhook.
      catalog: opcional, para resolver `handle → title` en list_reply. Si
        es None, los list_reply caen a "el cliente seleccionó: <id>".
      referral_already_seen: True si la sesión ya registró el ctwa_clid
        de este inbound (el banner se inyecta una sola vez).

    Returns:
      `EffectiveText` con el texto natural y metadata para el ingest.
    """
    tags: list[str] = [f"type:{msg.msg_type}"]

    # --- Audio: requiere transcripción aparte. El texto efectivo se
    #     conocerá DESPUÉS de la activity de transcribe. Acá solo
    #     marcamos el pendiente. ---
    if msg.audio:
        audio_id = msg.audio.get("id")
        tags.append("requires_transcription")
        return EffectiveText(
            text=None,
            requires_transcription=True,
            audio_media_id=audio_id if isinstance(audio_id, str) else None,
            referral_attribution=msg.referral,
            is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
            media_inbound_kind="audio",
            quoted_message_id=_qmid(msg),
            debug_tags=tags,
        )

    # --- Text passthrough ---
    if msg.text is not None and msg.text.strip():
        text = msg.text.strip()
        text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
        return EffectiveText(
            text=text,
            referral_attribution=msg.referral,
            is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
            quoted_message_id=_qmid(msg),
            debug_tags=tags,
        )

    # --- Interactive responses ---
    if msg.interactive:
        sub = msg.interactive.get("type")
        if sub == "button_reply":
            title = msg.interactive.get("title") or msg.interactive.get("id") or ""
            text = f"[el cliente tocó el botón: {title}]"
            structured = {
                "kind": "button_reply",
                "id": msg.interactive.get("id"),
                "title": title,
            }
            tags.append("interactive:button_reply")
            text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
            return EffectiveText(
                text=text,
                referral_attribution=msg.referral,
                is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
                structured_payload=structured,
                quoted_message_id=_qmid(msg),
                debug_tags=tags,
            )

        if sub == "list_reply":
            row_id = msg.interactive.get("id") or ""
            row_title = msg.interactive.get("title") or ""
            resolved_title = await _resolve_handle_title(catalog, row_id) if catalog else row_title
            display = resolved_title or row_title or row_id
            text = f"[el cliente seleccionó: {display}]"
            structured = {
                "kind": "list_reply",
                "id": row_id,
                "title": row_title,
                "description": msg.interactive.get("description"),
                "resolved_product_title": resolved_title,
            }
            tags.append("interactive:list_reply")
            text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
            return EffectiveText(
                text=text,
                referral_attribution=msg.referral,
                is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
                structured_payload=structured,
                quoted_message_id=_qmid(msg),
                debug_tags=tags,
            )

        if sub == "nfm_reply":
            response_json = msg.interactive.get("response_json")
            if isinstance(response_json, dict):
                fields = _format_flow_response(response_json)
                text = f"[datos de envío recibidos] {fields}"
            else:
                text = "[datos de envío recibidos]"
            structured = {
                "kind": "nfm_reply",
                "name": msg.interactive.get("name"),
                "response_json": response_json,
            }
            tags.append("interactive:nfm_reply")
            return EffectiveText(
                text=text,
                structured_payload=structured,
                quoted_message_id=_qmid(msg),
                debug_tags=tags,
            )

        # Unknown interactive subtype: passthrough con marca
        tags.append(f"interactive:{sub or 'unknown'}")
        return EffectiveText(
            text=f"[el cliente envió una interacción no soportada: {sub}]",
            structured_payload={"kind": "unknown_interactive", "raw": msg.interactive},
            debug_tags=tags,
        )

    # --- Location ---
    if msg.location:
        lat = msg.location.get("latitude")
        lng = msg.location.get("longitude")
        place = msg.location.get("name") or msg.location.get("address")
        if place:
            text = f"[el cliente compartió su ubicación: {place} (lat={lat}, lng={lng})]"
        else:
            text = f"[el cliente compartió su ubicación: lat={lat}, lng={lng}]"
        structured = {
            "kind": "location",
            "latitude": lat,
            "longitude": lng,
            "name": msg.location.get("name"),
            "address": msg.location.get("address"),
        }
        tags.append("location")
        text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
        return EffectiveText(
            text=text,
            referral_attribution=msg.referral,
            is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
            structured_payload=structured,
            quoted_message_id=_qmid(msg),
            debug_tags=tags,
        )

    # --- Order (cart submit via product browsing en WA) ---
    if msg.order:
        items = msg.order.get("product_items") or []
        item_summaries = []
        for it in items:
            pid = it.get("product_retailer_id", "?")
            qty = it.get("quantity", 1)
            item_summaries.append(f"{qty}× {pid}")
        summary = ", ".join(item_summaries) or "(carrito vacío)"
        order_text_note = msg.order.get("text") or ""
        text = f"[el cliente armó un carrito con: {summary}]"
        if order_text_note:
            text += f' "{order_text_note}"'
        structured = {
            "kind": "order",
            "catalog_id": msg.order.get("catalog_id"),
            "items": items,
            "text_note": order_text_note,
        }
        tags.append("order")
        text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
        return EffectiveText(
            text=text,
            referral_attribution=msg.referral,
            is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
            structured_payload=structured,
            quoted_message_id=_qmid(msg),
            debug_tags=tags,
        )

    # --- Contacts ---
    if msg.contacts:
        first = msg.contacts[0] if msg.contacts else {}
        name_obj = first.get("name") if isinstance(first, dict) else None
        formatted = (name_obj or {}).get("formatted_name", "(sin nombre)")
        text = f"[el cliente compartió un contacto: {formatted}]"
        tags.append("contacts")
        return EffectiveText(
            text=text,
            structured_payload={"kind": "contacts", "raw": msg.contacts},
            debug_tags=tags,
        )

    # --- Media no-audio (imagen/video/doc/sticker) ---
    if msg.media:
        kind = msg.media.get("type", "media")
        tags.append(f"media:{kind}")
        text = f"[el cliente envió un {kind}]"

        # Documento PDF (comprobante de pago típico): marker con el nombre del
        # archivo + metadata para que el ingest persista los bytes y el
        # operador pueda ABRIR el comprobante desde el dashboard. Otros mimes
        # de documento quedan en el marker genérico (sin descarga).
        doc_mime = (
            (msg.media.get("mime_type") or "").split(";")[0].strip().lower()
        )
        doc_id = msg.media.get("id")
        doc_filename = msg.media.get("filename")
        document_media_id: str | None = None
        document_filename: str | None = None
        if (
            kind == "document"
            and doc_mime == "application/pdf"
            and isinstance(doc_id, str)
            and doc_id
        ):
            tags.append("pdf_document")
            document_media_id = doc_id
            if isinstance(doc_filename, str) and doc_filename.strip():
                # Cap a 80 chars: el filename lo controla el cliente y viaja
                # al prompt del LLM y al chip del dashboard — sin cap, un
                # nombre de 200+ chars infla ambos.
                document_filename = doc_filename.strip()[:80]
                text = f"[el cliente envió un documento PDF: {document_filename}]"
            else:
                text = "[el cliente envió un documento PDF]"
            caption = msg.media.get("caption")
            if isinstance(caption, str) and caption.strip():
                text += f' con el texto: "{caption.strip()}"'

        text = _prepend_referral_banner_if_needed(text, msg.referral, referral_already_seen, tags)
        # Imágenes: marcamos requires_vision para que el ingest las describa
        # con un modelo multimodal (Gemini) y reinyecte la descripción como
        # texto. Solo "image" — video/sticker quedan en el placeholder (sticker
        # suele ser emoji) y los PDFs van por `document_media_id` (persistencia
        # sin visión, arriba). El `text` de arriba queda como fallback si la
        # visión está deshabilitada.
        image_id = msg.media.get("id") if kind == "image" else None
        wants_vision = kind == "image" and isinstance(image_id, str)
        if wants_vision:
            tags.append("requires_vision")
        return EffectiveText(
            text=text,
            requires_vision=wants_vision,
            image_media_id=image_id if isinstance(image_id, str) else None,
            image_mime_type=(
                msg.media.get("mime_type") if kind == "image" else None
            ),
            referral_attribution=msg.referral,
            is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
            media_inbound_kind=kind,
            quoted_message_id=_qmid(msg),
            document_media_id=document_media_id,
            document_mime_type=doc_mime if document_media_id else None,
            document_filename=document_filename,
            debug_tags=tags,
        )

    # --- Catch-all ---
    tags.append("empty_or_unsupported")
    return EffectiveText(
        text=None,
        referral_attribution=msg.referral,
        is_first_touch_from_ad=bool(msg.referral) and not referral_already_seen,
        debug_tags=tags,
    )


# =============================================================================
# Helpers
# =============================================================================


def _qmid(msg: WhatsAppMessage) -> str | None:
    if not msg.context:
        return None
    val = msg.context.get("id")
    return val if isinstance(val, str) else None


def _prepend_referral_banner_if_needed(
    text: str,
    referral: dict[str, Any] | None,
    already_seen: bool,
    tags: list[str],
) -> str:
    """Si el inbound trae referral y es la primera vez en la sesión, anteponemos
    un "banner" para que el LLM tenga contexto del origen.

    Una sola vez por sesión: el ingestor pasa `already_seen=True` desde el
    segundo turno en adelante. Después de eso el referral se persiste en
    state pero no se vuelve a inyectar al prompt.
    """
    if not referral or already_seen:
        return text
    headline = referral.get("headline")
    body = referral.get("body")
    source_type = referral.get("source_type")
    referred_product = referral.get("referred_product")

    parts = []
    if source_type == "ad":
        parts.append("[el cliente vino desde un anuncio de Facebook/Instagram")
    elif source_type == "post":
        parts.append("[el cliente vino desde un post de Facebook/Instagram")
    else:
        parts.append("[el cliente vino desde una atribución")
    if headline:
        parts.append(f"titulado '{headline}'")
    if body:
        parts.append(f"({body[:120]})")
    if referred_product:
        rp = referred_product
        parts.append(
            f"interesado en el producto retailer_id={rp.get('product_retailer_id')}"
        )
    banner = ", ".join(parts) + "]"
    tags.append("referral:banner_injected")
    return f"{banner}\n{text}"


def _format_flow_response(payload: dict[str, Any]) -> str:
    """Convierte el response_json del Flow a un string formato `k=v; k=v`.

    PREMORTEM #8: sanitizar values anidados. Si Meta agrega un campo con
    dict/list (ej. address={'street': 'X'}), str(v) queda como literal
    Python que el LLM no parsea bien. Acá serializamos dict/list a JSON
    compacto y truncamos strings largos.
    """
    import json as _json
    parts = []
    for k, v in payload.items():
        if v is None or v == "":
            continue
        if isinstance(v, (dict, list)):
            # Serializa explícitamente a JSON; truncamos si excede 200 chars
            # para no inundar el prompt.
            value_str = _json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            if len(value_str) > 200:
                value_str = value_str[:197] + "..."
        elif isinstance(v, (int, float, bool)):
            value_str = str(v)
        else:
            # string — truncar si excede 200 chars
            value_str = str(v)
            if len(value_str) > 200:
                value_str = value_str[:197] + "..."
        # Sanitizar el key — solo alfanuméricos + underscore para evitar
        # quiebres del prompt si Meta agrega keys con caracteres raros
        safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in str(k))
        parts.append(f"{safe_key}={value_str}")
    return "; ".join(parts) if parts else "(sin datos)"


async def _resolve_handle_title(catalog: "CatalogPort | None", handle: str) -> str | None:
    """Resuelve handle del producto a su title vía CatalogPort (snapshot).

    Si el catalog no resuelve (handle inválido, snapshot stale, port off),
    devuelve None y el caller usa el title raw del list_reply.
    """
    if not catalog or not handle:
        return None
    try:
        product = await catalog.get_by_handle(handle)
    except Exception:  # noqa: BLE001 - degradado, no roto
        return None
    return product.title if product else None
