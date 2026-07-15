"""Regla de oro del SDK: `src.sdk.mediakit` re-exporta el storage de media +
el upload a WhatsApp para plugins — mismo patrón que messagingkit/dashboardkit.

Consumidor: el plugin `chats` (endpoint `POST /media` del operador) sube y
persiste fotos salientes sin importar `src.platform` directo (P-28).

El check es por IDENTIDAD (`is`): la fachada re-exporta EL MISMO objeto que
platform, no una re-implementación.
"""
from __future__ import annotations


def test_mediakit_reexports_media_store():
    import src.platform.media.store as impl
    import src.sdk.mediakit as kit

    assert kit.persist_outbound_image is impl.persist_outbound_image
    assert kit.media_url_for is impl.media_url_for
    assert kit.is_safe_segment is impl.is_safe_segment


def test_mediakit_reexports_whatsapp_media_upload():
    import src.platform.whatsapp.client as impl
    import src.sdk.mediakit as kit

    assert kit.upload_media is impl.upload_media
    assert kit.MediaUploadError is impl.MediaUploadError
