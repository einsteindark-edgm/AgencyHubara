"""Storage de media inbound (imágenes de WhatsApp) para el dashboard.

Ver ``store.py`` para el detalle del layout y la motivación.
"""
from __future__ import annotations

from src.platform.media.store import (
    RETENTION_EPHEMERAL,
    RETENTION_RECEIPT,
    delete_outbound_image,
    is_safe_segment,
    media_url_for,
    persist_inbound_image,
    persist_outbound_image,
    resolve_media_file,
    retention_class_for,
)

__all__ = [
    "RETENTION_EPHEMERAL",
    "RETENTION_RECEIPT",
    "delete_outbound_image",
    "is_safe_segment",
    "media_url_for",
    "persist_inbound_image",
    "persist_outbound_image",
    "resolve_media_file",
    "retention_class_for",
]
