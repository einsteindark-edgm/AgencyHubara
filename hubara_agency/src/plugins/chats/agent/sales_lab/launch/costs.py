"""Costo estimado de una corrida y topes de gasto (plan §3.7 y §8.2). Puro.

Tarifas por turno y por pasada de un bot sobre el banco, medidas en
producción el 2026-09-23: el agente gastó US$7,09 en ~404 turnos; el juez de
una corrida de decisión, ~US$30 en 9 pasadas; Jev suma ~US$0,0002 por turno. Antes de comparar, la caja re-mide el
control real (A0) con el mismo juez: una pasada más sobre el banco. El gasto
real lo reporta la caja en `runs/<corrida>/progress.json` y la caja corta si
llega al tope.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.sdk.labkit import LabStorePort, split_arm

AGENT_USD_PER_TURN = 7.09 / 404
JUDGE_USD_PER_TURN = 30.0 / (9 * 404)
# B0 (workflow V2 con las reglas de hoy) no le pregunta nada a Jev.
PERCEPTION_USD_PER_TURN = {"A1": 0.0, "B0": 0.0, "B": 0.0002}

_BOGOTA = timezone(timedelta(hours=-5))


def estimate_run_usd(
    arms: list[str], *, reps: int, turns: int, control: bool = True, judge_usd_per_turn: float = 0.0
) -> float:
    """El juez es Claude Code (decisión del operador, 2026-09-28): califica fuera
    de la caja y no gasta API, así que por defecto no suma. Con el juez de pago
    (`LAB_JUDGE=litellm` en la caja) se pasa `JUDGE_USD_PER_TURN`."""
    judge = judge_usd_per_turn
    # Un brazo con paquete (`B@ventas-2`, F6) cuesta como su bot.
    per_turn = sum(AGENT_USD_PER_TURN + judge + PERCEPTION_USD_PER_TURN.get(split_arm(a)[0], 0.0) for a in arms) * reps
    if control and arms:
        per_turn += judge  # la pasada del juez sobre A0
    return round(per_turn * max(0, turns), 2)


_DATED_RUN_RE = re.compile(r"run-(\d{6})\d{2}-")


def _same_month(ms: int, now_ms: int) -> bool:
    a = datetime.fromtimestamp(ms / 1000, tz=_BOGOTA)
    b = datetime.fromtimestamp(now_ms / 1000, tz=_BOGOTA)
    return (a.year, a.month) == (b.year, b.month)


def month_spent_usd(store: LabStorePort, *, now_ms: int) -> float:
    """Gasto real de las corridas del mes en curso (hora de Bogotá). No lee las
    corridas cuyo id dice otro mes (`run-AAAAMMDD-…`, fecha de Bogotá); un id
    sin fecha se lee igual (subcontar el gasto es el error peligroso)."""
    total = 0.0
    month = f"{datetime.fromtimestamp(now_ms / 1000, tz=_BOGOTA):%Y%m}"
    for run_id in store.list_children("runs/"):
        dated = _DATED_RUN_RE.match(run_id)
        if dated and dated.group(1) != month:
            continue
        raw = store.get_bytes(f"runs/{run_id}/progress.json")
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
