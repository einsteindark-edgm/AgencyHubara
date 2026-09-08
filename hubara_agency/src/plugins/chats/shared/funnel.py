"""Predicados del embudo compartidos entre los agentes del plugin ``chats``.

``sales`` (cierre lazy por TIMEOUT) y ``remarketing`` (watchdog) necesitan la
misma respuesta a "¿este episodio tiene un carrito abierto?" para emitir
``CartAbandoned`` a Meta (auditoría CAPI 2026-09-08). Vive en ``shared/``
porque los agentes no pueden importarse entre sí (R-DIP #10).
"""
from __future__ import annotations

from typing import Any


def active_episode(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Último episodio si sigue abierto (``closed_at_ms`` nulo)."""
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return None
    last = episodes[-1]
    if not isinstance(last, dict) or last.get("closed_at_ms") is not None:
        return None
    return last


def is_open_cart(episode: dict[str, Any] | None, metadata: dict[str, Any]) -> bool:
    """True si el episodio dejó un pedido armado sin pagar.

    Carrito abierto = (a) el episodio tiene ``order_id`` (``register_order``
    lo anotó) y no cerró con venta, o (b) el borrador tiene ``producto``.
    Nunca después de un ``Purchase`` enviado (``capi_terminal_event``) ni
    con la sesión en ``COMPRA_EXITOSA``.
    """
    if not isinstance(episode, dict):
        return False
    if metadata.get("capi_terminal_event") == "Purchase":
        return False
    if metadata.get("tag") == "COMPRA_EXITOSA" or episode.get("closing_tag") == "COMPRA_EXITOSA":
        return False
    if episode.get("order_id"):
        return True
    draft = episode.get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) else None
    return bool(isinstance(slots, dict) and slots.get("producto"))


__all__ = ["active_episode", "is_open_cart"]
