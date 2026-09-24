"""Contrato `perception-rollout@v1` (plan del laboratorio PR 16): el encendido
del bot nuevo (capas con clasificador) por etapas.

  GET /api/chats/perception/rollout   estado, techo, chequeos por modo y métricas de la sombra
  PUT /api/chats/perception/rollout   {mode, canary_percent?, test_numbers?}

El panel de la sección Agents (`agents_admin`) lo consume por cast. El techo
lo fija Terraform (`SALES_PERCEPTION_MODE_CEILING`); este control mueve el
modo dentro del techo. Apagar y bajar siempre pasan (interruptor de
emergencia); subir sin cumplir los chequeos da 422 con los que fallan. El
cambio actúa en el siguiente mensaje de cada conversación (el ingest lee el
estado en cada mensaje).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from src.plugins.chats.agent.sales.perception.rollout import (
    MODES,
    RolloutFacts,
    RolloutState,
    can_set_mode,
    readiness,
)
from src.plugins.chats.agent.sales.perception.rollout_store import read_state, shadow_metrics, write_state
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()

_SID_RE = re.compile(r"^wa_\d{8,15}$")
_TARGETS = ("shadow", "canary", "on")


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ceiling() -> str:
    value = (os.getenv("SALES_PERCEPTION_MODE_CEILING") or "off").strip().lower()
    return value if value in MODES else "off"


def _facts(state: RolloutState) -> tuple[RolloutFacts, dict[str, Any]]:
    metrics = shadow_metrics(_vault_dir(), now_ms=_now_ms())
    facts = RolloutFacts(
        ceiling=_ceiling(),
        current=state.mode if state.mode in MODES else "off",
        signal_meta_enabled=(os.getenv("SALES_SIGNAL_INBOUND_META") or "").strip().lower() in {"on", "1", "true"},
        api_key_present=bool((os.getenv("OPENROUTER_API_KEY") or "").strip()),
        shadow_days=metrics.days,
        shadow_turns=metrics.turns,
        shadow_fallback_rate=metrics.fallback_rate,
        shadow_p95_ms=metrics.p95_ms,
    )
    return facts, asdict(metrics)


def _payload(state: RolloutState) -> dict[str, Any]:
    facts, metrics = _facts(state)
    return {
        "state": {**asdict(state), "test_numbers": list(state.test_numbers)},
        "ceiling": facts.ceiling,
        "profile": (os.getenv("SALES_PERCEPTION_PROFILE") or "jev-v1").strip(),
        "metrics": metrics,
        "readiness": {t: [asdict(c) for c in readiness(t, facts)] for t in _TARGETS},
        "can": {t: list(can_set_mode(t, facts)) for t in _TARGETS},
    }


@router.get("/perception/rollout")
def get_rollout() -> dict[str, Any]:
    return _payload(read_state(_vault_dir()))


@router.put("/perception/rollout")
def put_rollout(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    current = read_state(_vault_dir())
    mode = str(body.get("mode") or "")
    if mode not in MODES:
        raise HTTPException(422, detail={"reason": "invalid_mode", "message": "Modos: off, shadow, canary u on."})
    percent = body.get("canary_percent", current.canary_percent)
    if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
        raise HTTPException(422, detail={"reason": "invalid_percent", "message": "El porcentaje va de 0 a 100."})
    numbers = body.get("test_numbers", list(current.test_numbers))
    if not isinstance(numbers, list) or not all(isinstance(n, str) and _SID_RE.match(n) for n in numbers):
        raise HTTPException(422, detail={"reason": "invalid_test_numbers", "message": "Números de prueba como wa_57…"})
    facts, _ = _facts(current)
    failing = can_set_mode(mode, facts)
    if failing:
        raise HTTPException(
            422,
            detail={
                "reason": "not_ready",
                "failing": list(failing),
                "readiness": [asdict(c) for c in readiness(mode, facts)],
            },
        )
    state = RolloutState(
        mode=mode,
        canary_percent=percent,
        test_numbers=tuple(numbers),
        updated_at_ms=_now_ms(),
        updated_by="dashboard",
    )
    write_state(_vault_dir(), state)
    return _payload(state)
