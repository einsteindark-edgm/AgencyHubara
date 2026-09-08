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


def enqueue_capi_for_tag(
    metadata: dict[str, Any],
    *,
    tag: str,
    session_id: str,
    now_ms: int,
    source: str,
    episode_id: str | None = None,
) -> str | None:
    """Señal de embudo CAPI para un tag recién aplicado (tool del bot o
    endpoint humano/MBA): INTERESADO → QualifiedLead, CONFIRMADO_* →
    LeadSubmitted, COMPRA_EXITOSA → Purchase (con el ``registered_order``).
    Muta ``metadata`` (encola); devuelve el event_id o None. Nunca levanta."""
    from src.sdk.connectorkit import enqueue_capi_event

    ep_id = episode_id
    if not ep_id:
        episodes = metadata.get("episodes")
        last = episodes[-1] if isinstance(episodes, list) and episodes else None
        ep_id = str((last or {}).get("episode_id") or "") or None
    try:
        if tag == "INTERESADO" and ep_id:
            return enqueue_capi_event(
                metadata, event_name="QualifiedLead", session_id=session_id,
                episode_id=ep_id, source=source, now_ms=now_ms,
            )
        if tag in ("CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS") and ep_id:
            return enqueue_capi_event(
                metadata, event_name="LeadSubmitted", session_id=session_id,
                episode_id=ep_id, source=source, now_ms=now_ms,
            )
        if tag == "COMPRA_EXITOSA":
            reg = metadata.get("registered_order")
            if (
                isinstance(reg, dict)
                and reg.get("success")
                and isinstance(reg.get("order_id"), str)
                and isinstance(reg.get("total_cop"), int)
            ):
                return enqueue_capi_event(
                    metadata, event_name="Purchase", session_id=session_id,
                    order_id=reg["order_id"], value=reg["total_cop"],
                    currency=str(reg.get("currency") or "COP"), source=source, now_ms=now_ms,
                )
    except ValueError:
        return None
    return None


__all__ = ["active_episode", "is_open_cart", "enqueue_capi_for_tag"]
