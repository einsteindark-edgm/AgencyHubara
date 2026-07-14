"""Storage filesystem de imágenes inbound de WhatsApp para el dashboard.

Cuando un cliente manda una imagen por WhatsApp, el ingest la descarga de Meta
(via ``fetch_media_bytes``) y la persiste acá para que el operador humano la
pueda VER en el dashboard — clave para comprobantes de pago. Sin esto, la
imagen solo existía como descripción de texto generada por visión (Gemini) y
los bytes se descartaban tras la clasificación; el humano nunca veía la foto.

Layout en disco (mismo vault que el JSONL + metadata de la sesión)::

    <vault>/<session_id>/media/<filename>

El ``filename`` deriva del ``media_id`` de Meta (sanitizado) + la extensión del
mime. El endpoint ``GET /api/dashboard/media/{session_id}/{filename}``
(``dashboard.py``) sirve estos archivos al frontend.

NO es un port hexagonal con interface: igual que ``meta_media_fetcher``, es un
módulo de funciones planas (R-DIP no aplica — no cruza el workflow boundary, es
I/O del HTTP layer). En prod (S3 + CloudFront) se reemplazaría el cuerpo de
estas funciones por el SDK del object store sin tocar a los callers.
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.vision.dtos import VISION_KIND_PAYMENT_RECEIPT

logger = structlog.get_logger()

# --- Política de retención (Fase 0) ---------------------------------------
# Solo CLASIFICA: asigna a cada imagen una "clase de retención" que un futuro
# uploader a S3 traducirá a un object tag / prefijo para que **S3 Lifecycle**
# borre automáticamente (sin código ni cómputo). Fase 0 NO borra nada — solo
# deja el label persistido en el índice de media.
RETENTION_RECEIPT = "receipt"  # comprobantes de pago → retención larga
RETENTION_EPHEMERAL = "ephemeral"  # foto de producto / otro / visión fallida


def retention_class_for(kind: str | None) -> str:
    """Clase de retención de una imagen según su clasificación de visión.

    * ``comprobante_pago`` → ``receipt``: valor contable / disputa / chargeback,
      se necesita mucho después del episodio → retención larga.
    * cualquier otra cosa (foto de producto, ``otro``, o visión fallida) →
      ``ephemeral``: la descripción de texto ya quedó en el historial, así que
      la imagen pierde valor al cerrar el episodio → retención corta.
    """
    return (
        RETENTION_RECEIPT
        if kind == VISION_KIND_PAYMENT_RECEIPT
        else RETENTION_EPHEMERAL
    )

# Extensión por mime — WhatsApp manda jpeg/png/webp. Default jpg.
_MIME_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Charset permitido en segmentos de path que vienen del exterior (session_id +
# filename del endpoint). Incluye `+` porque los session_id de WhatsApp pueden
# llevar el prefijo E.164 (`wa_+573...`). Bloquea `/`, `..`, NUL, espacios —
# anti path-traversal (ni `/` ni `..` pasan el charset).
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._+-]+$")


def _ext_for_mime(mime_type: str | None) -> str:
    key = (mime_type or "").lower().split(";")[0].strip()
    return _MIME_EXT.get(key, ".jpg")


def _sanitize_media_id(media_id: str) -> str:
    """``media_id`` de Meta → token filesystem-safe.

    Meta usa dígitos largos, a veces con ``:`` o ``/``. Reemplazamos cualquier
    char fuera de ``[A-Za-z0-9._-]`` por ``_`` y capamos el largo.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", media_id)[:120]
    return safe or "img"


def is_safe_segment(segment: str) -> bool:
    """True si ``segment`` es un único componente de path seguro (sin `/`, `..`)."""
    return bool(_SAFE_SEGMENT.match(segment)) and ".." not in segment


def _media_dir(session_id: str) -> Path:
    return WORKSPACE_VAULT_DIR / session_id / "media"


def media_url_for(session_id: str, filename: str) -> str:
    """Ref relativa que se persiste en el JSONL y consume el dashboard.

    El frontend la vuelve absoluta prefijando ``env.apiUrl``.
    """
    return f"/api/dashboard/media/{session_id}/{filename}"


def persist_inbound_image(
    session_id: str, media_id: str, data: bytes, mime_type: str | None
) -> str:
    """Guarda los bytes en ``<vault>/<session_id>/media/<filename>``.

    Idempotente: si el archivo ya existe con tamaño > 0 (cliente reenvía el
    mismo media_id, o reentry doble), no reescribe. Devuelve el ``filename``
    para que el caller arme la URL con :func:`media_url_for`.
    """
    filename = f"{_sanitize_media_id(media_id)}{_ext_for_mime(mime_type)}"
    media_dir = _media_dir(session_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / filename
    if not (path.exists() and path.stat().st_size > 0):
        path.write_bytes(data)
    return filename


def persist_outbound_image(
    session_id: str, data: bytes, mime_type: str | None, *, token: str
) -> str:
    """Guarda una foto que el OPERADOR humano manda al cliente.

    Simétrico a :func:`persist_inbound_image` pero en el sentido saliente: el
    operador sube la foto desde el dashboard/app y la persistimos en el mismo
    vault para que el histórico del chat la pueda re-renderizar (el endpoint
    ``GET /api/dashboard/media/...`` la sirve sin cambios).

    A diferencia del inbound (keyed por el ``media_id`` de Meta, idempotente),
    acá el nombre deriva de un ``token`` opaco que genera el caller (uuid) — no
    tenemos el ``media_id`` hasta subir a Meta. El prefijo ``out-`` distingue
    salientes de entrantes en el mismo directorio. Devuelve el ``filename``.
    """
    # El token lo generamos nosotros (uuid) pero defendemos igual: a diferencia
    # de `_sanitize_media_id` (que preserva `.`), acá colapsamos TODO lo que no
    # sea alfanumérico/`_`/`-` — así ningún `..` sobrevive en el filename.
    safe_token = re.sub(r"[^A-Za-z0-9_-]", "_", token)[:120] or "img"
    filename = f"out-{safe_token}{_ext_for_mime(mime_type)}"
    media_dir = _media_dir(session_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / filename).write_bytes(data)
    return filename


def delete_outbound_image(session_id: str, filename: str) -> None:
    """Borra (best-effort) una foto saliente persistida por
    :func:`persist_outbound_image` que quedó huérfana.

    PM2-B7: la fase A persiste el archivo ANTES de subir a Meta; si la subida
    falla, cada retry del operador genera OTRO uuid → OTRO archivo, y nadie
    referencia ni limpia los anteriores (basura permanente en el vault). Solo
    acepta filenames ``out-*`` (jamás borra media inbound del cliente) y aplica
    las mismas guardas anti-traversal que el resto del módulo. Nunca lanza.
    """
    if not is_safe_segment(session_id) or not is_safe_segment(filename):
        return
    if not filename.startswith("out-"):
        return
    media_dir = _media_dir(session_id).resolve()
    path = (media_dir / filename).resolve()
    if media_dir != path.parent:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "delete_outbound_image failed (best-effort)",
            session_id=session_id,
            filename=filename,
        )


def resolve_media_file(session_id: str, filename: str) -> Path | None:
    """Resuelve el archivo a servir, con guardas anti path-traversal.

    Devuelve ``None`` si algún segmento es inseguro, si el path resuelto se
    escapa del directorio de media de la sesión, o si el archivo no existe.
    """
    if not is_safe_segment(session_id) or not is_safe_segment(filename):
        return None
    media_dir = _media_dir(session_id).resolve()
    path = (media_dir / filename).resolve()
    # Defensa en profundidad: el archivo resuelto DEBE quedar dentro de media_dir.
    if media_dir != path.parent:
        return None
    if not path.is_file():
        return None
    return path
