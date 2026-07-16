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


def test_mediakit_reexports_image_label_derivation():
    """`derive_image_label` vive en platform (2026-07-16, lo usa también el
    mapper de Meta Catalog); los plugins lo consumen vía este kit (P-28)."""
    import src.platform.catalog.image_labels as impl
    import src.sdk.mediakit as kit

    assert kit.derive_image_label is impl.derive_image_label
    assert kit.fold_for_match is impl.fold_for_match
