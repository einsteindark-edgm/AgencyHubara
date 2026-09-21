"""Foto del pedido listo — el ETA la manda con la plantilla de encabezado imagen.

El operador sube la foto al pedido desde el panel derecho de Órdenes; la API de
Órdenes guarda los BYTES en ``<vault>/<session>/media/`` y el puntero en
``metadata.order_photos[<order_id>]`` (``filename``, ``media_ref``, ``mime``,
``uploaded_at_ms``). Acá, al enviar:

1. Se sube la foto a Meta (``upload_media`` → ``media_id``). No se guarda el
   media_id al subir la foto porque Meta lo vence a los ~30 días y un pedido
   puede quedarse días en preparación. Sí se cachea al enviar (ligado a esa
   foto) para que un REINTENTO de la activity no la suba ni la envíe dos
   veces: mismo media_id → mismo fingerprint → la idempotencia de plantillas
   de ``send_template_to_session`` lo cubre.
2. Se manda ``order_ready_photo_utility_v1`` con la referencia del pedido
   (``OrderFacts.display_id``, gotcha 13) y la foto en el encabezado. La foto
   queda en el historial del chat (``image_url``).
3. Se registra en el timeline ETA como notificación de ``ready`` (así pasar a
   listo después de un envío manual no repite el aviso) sin retroceder la etapa
   si el pedido ya iba más adelante.

Errores de Meta (plantilla no aprobada, número inválido…) propagan como
``ApplicationError`` desde ``send_template_to_session``: el workflow decide
(respaldo con el aviso de estado en el camino automático).
"""
from __future__ import annotations

import os
import time
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.eta.agent.eta.activities.tracking import (
    _empty_entry,
    _mutate_orders,
    order_photo_entry,
)
from src.sdk.connectorkit import get_order_facts_port
from src.sdk.mediakit import is_safe_segment, upload_media
from src.sdk.messagingkit import send_template_to_session
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore, with_heartbeat

READY_PHOTO_TEMPLATE = "order_ready_photo_utility_v1"

#: Un media_id de Meta dura ~30 días; lo reusamos solo mientras es joven.
_MEDIA_ID_TTL_MS = 20 * 24 * 3600 * 1000
#: Tope del slot ``order_reference`` en el catálogo.
_REFERENCE_MAX = 60
_STAGE_ORDER = ("preparing", "ready", "shipping", "delivered", "cancelled")


def _store() -> FilesystemMetadataStore:
    return FilesystemMetadataStore(WORKSPACE_VAULT_DIR)


def _read(store: FilesystemMetadataStore, session_id: str) -> dict[str, Any]:
    try:
        data = store.read(session_id)
    except Exception:  # noqa: BLE001 — sesión inexistente = sin foto
        return {}
    return data if isinstance(data, dict) else {}


def _cached_media_id(photo: dict[str, Any], now_ms: int) -> str | None:
    media_id = photo.get("meta_media_id")
    if not isinstance(media_id, str) or not media_id:
        return None
    if photo.get("meta_media_for_uploaded_at_ms") != photo.get("uploaded_at_ms"):
        return None  # la foto cambió desde que se subió a Meta
    uploaded = photo.get("meta_media_uploaded_at_ms")
    if not isinstance(uploaded, int) or now_ms - uploaded > _MEDIA_ID_TTL_MS:
        return None
    return media_id


def _update_photo(
    store: FilesystemMetadataStore,
    session_id: str,
    order_id: str,
    uploaded_at_ms: Any,
    fields: dict[str, Any],
) -> None:
    """Merge atómico en la entrada de la foto, solo si sigue siendo LA MISMA
    foto (el operador pudo reemplazarla mientras enviábamos)."""

    def _apply(fresh: dict[str, Any]) -> dict[str, Any] | None:
        entry = (fresh.get("order_photos") or {}).get(order_id)
        if not isinstance(entry, dict) or entry.get("uploaded_at_ms") != uploaded_at_ms:
            return None
        for key, value in fields.items():
            if value is None:
                entry.pop(key, None)  # None = borrar (p.ej. el error tras un éxito)
            else:
                entry[key] = value
        return fresh

    store.update(session_id, _apply)


async def _order_reference(order_id: str) -> str:
    """``#31`` desde OrderFacts (Medusa vivo); si no responde, el id crudo."""
    reference = order_id
    try:
        snapshot = await get_order_facts_port().get_facts([order_id])
        fact = snapshot.facts.get(order_id) if snapshot is not None else None
        display = getattr(fact, "display_id", None)
        if isinstance(display, str) and display.strip():
            reference = display.strip()
    except Exception:  # noqa: BLE001 — Medusa caído: la referencia cruda sirve
        activity.logger.warning("send_ready_photo: facts de %s no disponibles", order_id)
    return reference[:_REFERENCE_MAX]


def _record_sent(store: FilesystemMetadataStore, session_id: str, order_id: str, reference: str) -> None:
    def _apply(orders: dict[str, dict[str, Any]]) -> None:
        entry = orders.get(order_id) or _empty_entry(order_id)
        notified = list(entry.get("notified_stages") or [])
        if "ready" not in notified:
            notified.append("ready")
        entry["notified_stages"] = notified
        current = entry.get("current_stage")
        if current not in _STAGE_ORDER or _STAGE_ORDER.index(current) < _STAGE_ORDER.index("ready"):
            entry["current_stage"] = "ready"
        events = list(entry.get("events") or [])
        events.append(
            {
                "stage": "ready",
                "agent_msg": f"[Foto del pedido {reference} enviada por plantilla]",
                "at_ms": int(time.time() * 1000),
                "reply": None,
                "flagged": False,
                "flag": None,
            }
        )
        entry["events"] = events
        orders[order_id] = entry

    _mutate_orders(store, session_id, _apply)


def _operator_error(exc: BaseException) -> str:
    """Texto para el panel de Órdenes: el envío corre en el ETA, lejos del
    clic, así que el motivo tiene que quedar escrito donde el operador mira."""
    raw = str(exc)
    if "132001" in raw:
        return (
            "La plantilla de pedido listo aún no está aprobada en Meta: la foto "
            "no se pudo enviar."
        )
    return f"No se pudo enviar la foto por WhatsApp: {raw[:240]}"


@activity.defn(name="send_ready_photo_activity")
@with_heartbeat(every=10)
async def send_ready_photo_activity(session_id: str, order_id: str) -> dict[str, Any]:
    """Manda la foto del pedido. ``{"sent": True, "wa_message_id": ...}`` o
    ``{"sent": False, "reason": ...}`` si el pedido no tiene foto. Un fallo de
    Meta queda en ``order_photos[...].last_send_error`` y se re-lanza."""
    store = _store()
    data = _read(store, session_id)
    photo = order_photo_entry(data, order_id)
    if photo is None:
        return {"sent": False, "reason": "no_photo"}
    try:
        return await _send(store, data, photo, session_id, order_id)
    except Exception as exc:
        _update_photo(
            store, session_id, order_id, photo.get("uploaded_at_ms"),
            {"last_send_error": _operator_error(exc), "last_send_error_at_ms": int(time.time() * 1000)},
        )
        raise


async def _send(
    store: FilesystemMetadataStore,
    data: dict[str, Any],
    photo: dict[str, Any],
    session_id: str,
    order_id: str,
) -> dict[str, Any]:
    filename = photo["filename"]
    path = WORKSPACE_VAULT_DIR / session_id / "media" / filename
    if not is_safe_segment(filename) or not path.is_file():
        activity.logger.warning("send_ready_photo: falta el archivo %s de %s", filename, order_id)
        _update_photo(
            store, session_id, order_id, photo.get("uploaded_at_ms"),
            {"last_send_error": "No se encontró el archivo de la foto: vuelve a subirla."},
        )
        return {"sent": False, "reason": "photo_file_missing"}

    now_ms = int(time.time() * 1000)
    media_id = _cached_media_id(photo, now_ms)
    if media_id is None:
        phone_number_id = data.get("phone_number_id") or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        if not phone_number_id:
            raise ApplicationError(
                "WHATSAPP_PHONE_NUMBER_ID no configurado", non_retryable=True
            )
        media_id = await upload_media(
            phone_number_id, path.read_bytes(), photo.get("mime") or "image/jpeg"
        )
        _update_photo(
            store,
            session_id,
            order_id,
            photo.get("uploaded_at_ms"),
            {
                "meta_media_id": media_id,
                "meta_media_uploaded_at_ms": now_ms,
                "meta_media_for_uploaded_at_ms": photo.get("uploaded_at_ms"),
            },
        )

    reference = await _order_reference(order_id)
    result = await send_template_to_session(
        session_id,
        READY_PHOTO_TEMPLATE,
        {"order_reference": reference},
        header_media_id=media_id,
        header_image_url=photo.get("media_ref"),
    )

    wa_message_id = getattr(result, "wa_message_id", None)
    # Reenvío de la MISMA foto dentro de la ventana de idempotencia: el envío
    # anterior devuelve su mismo wamid y a Meta no sale nada. No es un envío
    # nuevo → ni otra entrada en el timeline ni otro sent_at.
    if wa_message_id and wa_message_id == photo.get("last_wa_message_id"):
        activity.logger.info("send_ready_photo: %s ya enviado (%s) — dedup", order_id, wa_message_id)
        return {"sent": True, "wa_message_id": wa_message_id, "deduped": True}

    _update_photo(
        store, session_id, order_id, photo.get("uploaded_at_ms"),
        {
            "sent_at_ms": int(time.time() * 1000),
            "last_wa_message_id": wa_message_id,
            "last_send_error": None,
        },
    )
    _record_sent(store, session_id, order_id, reference)
    activity.logger.info("send_ready_photo: foto de %s enviada a %s", order_id, session_id)
    return {"sent": True, "wa_message_id": wa_message_id}
