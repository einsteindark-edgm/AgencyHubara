"""Origen de una conversación para el dashboard (puro, sin I/O).

El ingest persiste dos cosas (`ingest_inbound_message._handle_origin` +
`episode_lifecycle.ensure_active_episode`):

  * ``metadata.origin`` — first-touch STICKY de la sesión
    (channel / headline / source_id / first_seen_ms).
  * ``episodes[*].referral_snapshot`` — el referral con el que arrancó (o se
    re-atribuyó, last-ad-touch) CADA episodio.

Para "de qué campaña llegó esta conversación" manda el snapshot del ÚLTIMO
episodio (es lo que el ads plugin atribuye); el sticky solo aporta el
first-touch cuando el episodio no trae referral. `source_id` es el ad id —
el nombre real de la campaña lo resuelve el caller (best-effort, Graph API)
y lo deja en `campaign_name` / `ad_name`.
"""
from __future__ import annotations

from typing import Any

ORIGIN_KEYS = ("channel", "source_id", "source_type", "headline")


def _last_episode_snapshot(metadata: dict[str, Any]) -> dict[str, Any] | None:
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return None
    last = episodes[-1]
    snap = last.get("referral_snapshot") if isinstance(last, dict) else None
    return snap if isinstance(snap, dict) and snap.get("channel") else None


def session_origin(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """metadata.json → origen plano para el dashboard, o None si nunca hubo
    inbound clasificado (sesión sin `origin` ni episodios con referral)."""
    if not metadata:
        return None
    sticky = metadata.get("origin")
    sticky = sticky if isinstance(sticky, dict) else {}
    source = _last_episode_snapshot(metadata) or sticky
    if not source or not source.get("channel"):
        return None
    first_seen = sticky.get("first_seen_ms")
    if not isinstance(first_seen, int):
        episodes = metadata.get("episodes") or []
        first = episodes[0] if episodes and isinstance(episodes[0], dict) else {}
        first_seen = first.get("started_at_ms") if isinstance(first.get("started_at_ms"), int) else None
    return {
        "channel": source.get("channel"),
        "source_id": source.get("source_id"),
        "source_type": source.get("source_type"),
        "headline": source.get("headline"),
        "first_seen_ms": first_seen,
        "campaign_name": None,
        "ad_name": None,
    }


def with_ad_names(
    origin: dict[str, Any] | None, names: dict[str, dict[str, str | None]]
) -> dict[str, Any] | None:
    """Completa `campaign_name` / `ad_name` desde el batch de Graph (si hay)."""
    if origin is None:
        return None
    info = names.get(origin.get("source_id") or "") or {}
    return {
        **origin,
        "campaign_name": info.get("campaign_name"),
        "ad_name": info.get("ad_name"),
    }
