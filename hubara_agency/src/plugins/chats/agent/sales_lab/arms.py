"""Brazos simulados del laboratorio (plan §3.1 y PR 15; motor de decisiones
§08). PURO.

Cada brazo es un bot del registro de bots (`sales/decisions/bots.py`): la
versión del workflow, el proveedor de cada capacidad y el perfil de Jev. El
sandbox lo fija para todo el caso (`DECISIONS_BOT`); la señal lleva el modo de
las capas ①②③ y el perfil, exactamente como una conversación en canary de
producción.

  A1  el bot actual: workflow V1, la señal de hoy (3 argumentos, sin modo),
      todo en reglas
  B0  el workflow V2 con reglas y la señal de hoy: tiene que dar lo mismo que
      A1 (prueba que el esqueleto nuevo es fiel)
  B   el bot nuevo: workflow V2 con Jev

A0 no se simula (es lo que pasó de verdad). El brazo C (OpenAI) se quitó el
2026-09-28: 100 % Jev.

Un brazo puede fijar el paquete de decisión: `B@ventas-2` es el bot B con la
inteligencia de `ventas-2` (PAQUETES_DE_DECISION.md F6). El proceso del caso
lo fija ANTES de importar la app (`arm_env`), así las capacidades y el
vocabulario de las tools salen de ese paquete.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import LAB_BOTS, bot_for_arm
from src.plugins.chats.shared.store_pack import BUNDLE_ENV
from src.sdk.labkit import arm_pattern, split_arm

SIMULATED_ARMS: tuple[str, ...] = tuple(LAB_BOTS)
#: Perfil de Jev de cada brazo con capas (lo lee la API del laboratorio).
ARM_PROFILES: dict[str, str] = {arm: bot.profile for arm, bot in LAB_BOTS.items() if bot.layers != "off"}
_RUNNABLE = arm_pattern(SIMULATED_ARMS)


def runnable_arm(arm: str) -> bool:
    """¿El simulador sabe correr este brazo? Solo la forma (PURO: lo usa el
    workflow de la corrida); que el paquete exista lo valida el lanzador."""
    return bool(_RUNNABLE.fullmatch(arm))


def arm_profile(arm: str) -> str | None:
    """El perfil de Jev del brazo (el de su bot), o None si no usa capas."""
    return ARM_PROFILES.get(split_arm(arm)[0])


def arm_env(arm: str) -> dict[str, str]:
    """Las variables del proceso del caso para este brazo: el paquete fijado."""
    bundle = split_arm(arm)[1]
    return {BUNDLE_ENV: bundle} if bundle else {}


def signal_meta(arm: str, message: dict[str, Any]) -> dict[str, Any] | None:
    """El 4.º argumento de `send_message` para un mensaje de la ráfaga.
    None = la señal de hoy. El `wamid` del mensaje real viaja: el motor saca
    la ráfaga del historial con él (F1); el sandbox, como el ingest, ya dejó
    la ráfaga en el historial antes del turno (`sandbox/readings.py`)."""
    if not runnable_arm(arm):
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
