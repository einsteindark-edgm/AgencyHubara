"""Retener UNA vez por mensaje del cliente lo que una tool iba a enviar.

`send_reply` y las tools de tarjetas devuelven el texto al modelo (con lo que
tiene que corregir) en vez de enviarlo. Para que nunca sea un bucle, cada
motivo (`key`) retiene una sola vez por mensaje del cliente
(`last_inbound_message_id`): el segundo intento sale. Sin vault, sin mensaje
del cliente o si no se puede anotar la marca, el texto sale como siempre.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.agent.sales.state import FilesystemMetadataStore


def held_before(vault_dir: Path | None, session_key: str, key: str) -> bool:
    """¿Ya se retuvo un texto por `key` en este mensaje del cliente?"""
    if vault_dir is None:
        return False
    try:
        metadata = FilesystemMetadataStore(Path(vault_dir)).read(session_key) or {}
    except Exception:  # noqa: BLE001 — ilegible: `hold_once` decide
        return False
    inbound = metadata.get("last_inbound_message_id")
    return bool(inbound) and metadata.get(key) == inbound


def hold_once(
    vault_dir: Path | None, session_key: str, key: str, *, tool: str, error: str, message: str,
    status_key: str = "sent",
) -> str | None:
    """El envelope que devuelve el texto al modelo, si todavía no se retuvo
    uno por `key` en este mensaje del cliente (y lo anota); None = sale.
    `status_key`: «sent» para `send_reply`, «queued» para una tarjeta."""
    if vault_dir is None:
        return None
    store = FilesystemMetadataStore(Path(vault_dir))
    try:
        metadata = store.read(session_key) or {}
    except Exception:  # noqa: BLE001 — sin metadata legible: el texto sale
        return None
    inbound = metadata.get("last_inbound_message_id")
    if not inbound or metadata.get(key) == inbound:
        return None

    def _mark(fresh: dict[str, Any]) -> dict[str, Any] | None:
        if not fresh:
            return None
        fresh[key] = inbound
        return fresh

    try:
        if store.update(session_key, _mark) is None:
            return None
    except Exception:  # noqa: BLE001 — sin la marca podría repetirse: el texto sale
        return None
    logger.info("💬 [TOOL {}] retenido ({}) session={}", tool, error, session_key)
    return json.dumps({status_key: False, "error": error, "message": message}, ensure_ascii=False)


__all__ = ["held_before", "hold_once"]
