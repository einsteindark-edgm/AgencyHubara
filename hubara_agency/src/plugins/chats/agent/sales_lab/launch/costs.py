"""Costo estimado de una corrida y topes de gasto (plan §3.7 y §8.2). Puro.

Tarifas por turno y por pasada de un bot sobre el banco, medidas en
producción el 2026-09-23: el agente gastó US$7,09 en ~404 turnos; el juez de
una corrida de decisión, ~US$30 en 9 pasadas; el clasificador suma ~US$0,0002
(Jev) o ~US$0,0006 (OpenAI) por turno. El gasto real lo reporta la caja en
`runs/<corrida>/progress.json` y la caja corta si llega al tope.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.sdk.labkit import LabStorePort

AGENT_USD_PER_TURN = 7.09 / 404
JUDGE_USD_PER_TURN = 30.0 / (9 * 404)
PERCEPTION_USD_PER_TURN = {"A1": 0.0, "B": 0.0002, "C": 0.0006}

_BOGOTA = timezone(timedelta(hours=-5))


def estimate_run_usd(arms: list[str], *, reps: int, turns: int) -> float:
    per_turn = sum(AGENT_USD_PER_TURN + JUDGE_USD_PER_TURN + PERCEPTION_USD_PER_TURN.get(a, 0.0) for a in arms)
    return round(per_turn * reps * max(0, turns), 2)


def _same_month(ms: int, now_ms: int) -> bool:
    a = datetime.fromtimestamp(ms / 1000, tz=_BOGOTA)
    b = datetime.fromtimestamp(now_ms / 1000, tz=_BOGOTA)
    return (a.year, a.month) == (b.year, b.month)


def month_spent_usd(store: LabStorePort, *, now_ms: int) -> float:
    """Gasto real de las corridas del mes en curso (hora de Bogotá)."""
    total = 0.0
    for key in store.list_keys("runs/"):
        if not key.endswith("/progress.json"):
            continue
        raw = store.get_bytes(key)
        try:
            progress = json.loads(raw or b"{}")
        except ValueError:
            continue
        started = progress.get("started_at_ms") if isinstance(progress, dict) else None
        spent = progress.get("spent_usd") if isinstance(progress, dict) else None
        if isinstance(started, (int, float)) and isinstance(spent, (int, float)) and _same_month(int(started), now_ms):
            total += float(spent)
    return round(total, 2)


@dataclass(frozen=True)
class CapCheck:
    fits: bool
    reason: str | None  # run_cap | month_cap
    month_left: float


def check_caps(estimate: float, *, run_cap: float, month_cap: float, month_spent: float) -> CapCheck:
    left = round(month_cap - month_spent, 2)
    if estimate > run_cap:
        return CapCheck(False, "run_cap", left)
    if estimate > left:
        return CapCheck(False, "month_cap", left)
    return CapCheck(True, None, left)
