"""Brazos simulados del laboratorio (plan §3.1 y PR 15). PURO.

  A1  el bot actual: la señal de hoy (3 argumentos, sin modo)
  B   el bot nuevo con Jev (perfil `jev-v1`)

El bot nuevo recibe el modo `on` y su perfil en el 4.º argumento de la señal
(`inbound_meta`), exactamente como una conversación en canary de producción:
el mismo workflow, las mismas capas. A0 no se simula (es lo que pasó de
verdad). El brazo C (OpenAI) se quitó el 2026-09-28: 100 % Jev.
"""
from __future__ import annotations

from typing import Any

ARM_PROFILES: dict[str, str] = {"B": "jev-v1"}
SIMULATED_ARMS: tuple[str, ...] = ("A1", *ARM_PROFILES)


def signal_meta(arm: str, message: dict[str, Any]) -> dict[str, Any] | None:
    """El 4.º argumento de `send_message` para un mensaje de la ráfaga.
    None = la señal de hoy. El `wamid` del mensaje real viaja: el motor saca
    la ráfaga del historial con él (F1); en el sandbox el historial viene
    cortado al inicio del turno, así el contexto queda igual que en producción."""
    if arm not in SIMULATED_ARMS:
        raise ValueError(f"brazo desconocido: {arm!r} (se simulan {', '.join(SIMULATED_ARMS)})")
    profile = ARM_PROFILES.get(arm)
    if profile is None:
        return None
    meta: dict[str, Any] = {"perception_mode": "on", "perception_profile": profile}
    ts_ms = message.get("ts_ms")
    if isinstance(ts_ms, int) and not isinstance(ts_ms, bool):
        meta["ts_ms"] = ts_ms
    kind = message.get("kind")
    meta["kind"] = kind if isinstance(kind, str) and kind else "text"
    wamid = message.get("wamid")
    if isinstance(wamid, str) and wamid:
        meta["wamid"] = wamid
    return meta
