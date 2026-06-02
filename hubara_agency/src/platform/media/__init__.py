"""Storage de media inbound (imágenes de WhatsApp) para el dashboard.

Ver ``store.py`` para el detalle del layout y la motivación.
"""
from __future__ import annotations

from src.platform.media.store import (
    is_safe_segment,
    media_url_for,
    persist_inbound_image,
    resolve_media_file,
)

__all__ = [
    "is_safe_segment",
    "media_url_for",
    "persist_inbound_image",
    "resolve_media_file",
]
