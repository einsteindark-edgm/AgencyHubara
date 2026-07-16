"""MediaKit — storage de media + upload a WhatsApp, para plugins.

Fachada SDK (P-28) sobre `src.platform.media` y `src.platform.whatsapp.client`:
la forma sancionada de que un plugin (hoy `chats`, endpoint `POST /media` del
operador) persista una foto saliente en el vault, la exponga por URL servible,
y suba sus bytes a Meta para obtener un `media_id`.

Uso canónico (endpoint HTTP de un plugin)::

    from src.sdk.mediakit import (
        MediaUploadError,
        media_url_for,
        persist_outbound_image,
        upload_media,
    )

    filename = persist_outbound_image(session_id, content, mime, token=uuid)
    media_ref = media_url_for(session_id, filename)
    media_id = await upload_media(phone_number_id, content, mime)

`upload_media` sube bytes y devuelve el `media_id`; el ENVÍO del mensaje sigue
pasando por las activities de platform (`send_image_to_session`) — este kit es
la preparación de media, no el send.
"""
from __future__ import annotations

# Storage de media (persistir + resolver URL + guard anti-traversal). Alias
# idiom (regla 1 SDK): sin el `as x`, ruff --fix poda el re-export.
from src.platform.media import (
    delete_outbound_image as delete_outbound_image,
    is_safe_segment as is_safe_segment,
    media_url_for as media_url_for,
    persist_outbound_image as persist_outbound_image,
)

# Upload de bytes a WhatsApp Cloud API (POST /{phone_id}/media → media_id).
from src.platform.whatsapp.client import (
    MediaUploadError as MediaUploadError,
    upload_media as upload_media,
)

# Etiqueta de diseño derivada del filename de una imagen del catálogo
# (`Leo-01KX...webp` → "Leo"). Vive en platform porque el mapper de Meta
# Catalog también la usa (item per-variante, 2026-07-16).
from src.platform.catalog.image_labels import (
    derive_image_label as derive_image_label,
)
