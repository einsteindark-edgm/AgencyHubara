"""Brazos simulados del laboratorio (plan §3.1 y PR 15; motor de decisiones
§08). PURO.

Cada brazo es un bot del registro de bots (`sales/decisions/bots.py`): la
versión del workflow, el proveedor de cada capacidad y el perfil de Jev. El
sandbox lo fija para todo el caso (`DECISIONS_BOT`); la señal lleva el modo de
las capas ①②③ y el perfil, exactamente como una conversación en canary de
producción.

  A1  el bot actual: la señal de hoy (3 argumentos, sin modo), todo en reglas
  B   el bot nuevo con Jev

A0 no se simula (es lo que pasó de verdad). El brazo C (OpenAI) se quitó el
2026-09-28: 100 % Jev.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import LAB_BOTS, bot_for_arm

SIMULATED_ARMS: tuple[str, ...] = tuple(LAB_BOTS)
#: Perfil de Jev de cada brazo con capas (lo lee la API del laboratorio).
ARM_PROFILES: dict[str, str] = {arm: bot.profile for arm, bot in LAB_BOTS.items() if bot.layers != "off"}


def signal_meta(arm: str, message: dict[str, Any]) -> dict[str, Any] | None:
    """El 4.º argumento de `send_message` para un mensaje de la ráfaga.
    None = la señal de hoy. El `wamid` del mensaje real viaja: el motor saca
    la ráfaga del historial con él (F1); en el sandbox el historial viene
    cortado al inicio del turno, así el contexto queda igual que en producción."""
    if arm not in SIMULATED_ARMS:
        raise ValueError(f"brazo desconocido: {arm!r} (se simulan {', '.join(SIMULATED_ARMS)})")
    bot = bot_for_arm(arm)
    if bot.layers == "off":
        return None
    meta: dict[str, Any] = {"perception_mode": bot.layers, "perception_profile": bot.profile}
    ts_ms = message.get("ts_ms")
    if isinstance(ts_ms, int) and not isinstance(ts_ms, bool):
        meta["ts_ms"] = ts_ms
    kind = message.get("kind")
    meta["kind"] = kind if isinstance(kind, str) and kind else "text"
    wamid = message.get("wamid")
    if isinstance(wamid, str) and wamid:
        meta["wamid"] = wamid
    # Lo que escribió el cliente, sin lo que agregó el ingest: es lo que lee
    # el clasificador en producción (`inbound_meta.text`).
    raw = message.get("raw_text")
    if isinstance(raw, str) and raw.strip():
        meta["text"] = raw
    return meta
