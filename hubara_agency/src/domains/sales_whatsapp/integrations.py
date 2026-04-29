"""DEPRECATED. El cliente HTTP de WhatsApp vive ahora en
`src.core.infrastructure.whatsapp.client`. Este modulo se mantiene como shim hasta
que se confirme que ningun call site externo lo importa.
"""
from __future__ import annotations

from src.core.infrastructure.whatsapp.client import send_message  # noqa: F401
