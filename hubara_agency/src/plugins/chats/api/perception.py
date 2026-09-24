"""Contrato `perception-rollout@v1` (plan del laboratorio PR 16): el encendido
del bot nuevo (capas con clasificador) por etapas.

  GET /api/chats/perception/rollout   estado, techo, chequeos por modo y métricas de la sombra
  PUT /api/chats/perception/rollout   {mode, canary_percent?, test_numbers?}

El panel de la sección Agents (`agents_admin`) lo consume por cast. El techo
lo fija Terraform (`SALES_PERCEPTION_MODE_CEILING`); este control mueve el
modo dentro del techo. Apagar y bajar siempre pasan (interruptor de
emergencia): escriben sin recorrer el vault ni revalidar lo guardado. Subir
sin cumplir los chequeos da 422 con los que fallan. El cambio actúa en el
siguiente mensaje de cada conversación (el ingest lee el estado en cada
mensaje). Cada cambio queda firmado (`updated_by`) y en el log.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Body, HTTPException, Request

from src.plugins.chats.agent.sales.perception.rollout import (
    MODES,
    RolloutFacts,
    RolloutState,
    can_set_mode,
    readiness,
)
from src.plugins.chats.agent.sales.perception.rollout_store import (
    ShadowMetrics,
    read_state,
    shadow_metrics,
    write_state,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR

logger = structlog.get_logger()

router = APIRouter()

_SID_RE = re.compile(r"wa_\d{8,15}")
_TARGETS = ("shadow", "canary", "on")


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ceiling() -> str:
    value = (os.getenv("SALES_PERCEPTION_MODE_CEILING") or "off").strip().lower()
    return value if value in MODES else "off"


def _profile() -> str:
    return (os.getenv("SALES_PERCEPTION_PROFILE") or "jev-v1").strip()


def _api_key_present() -> bool:
    """El placeholder de Terraform no es una llave: el adaptador lo trata como
    ausente (todas las percepciones caerían con `no_api_key`)."""
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return bool(key) and not key.upper().startswith("PLACEHOLDER")


def _metrics() -> ShadowMetrics:
    try:
        return shadow_metrics(_vault_dir(), now_ms=_now_ms(), profile=_profile())
    except Exception as exc:  # noqa: BLE001 — sin métricas la sombra cuenta como cero: nadie sube por error
        logger.warning("perception.shadow_metrics_failed", error=repr(exc)[:200])
        return ShadowMetrics(days=0, turns=0, fallback_rate=None, p95_ms=None)


def _facts(state: RolloutState) -> tuple[RolloutFacts, dict[str, Any]]:
    metrics = _metrics()
    facts = RolloutFacts(
        ceiling=_ceiling(),
        current=state.mode if state.mode in MODES else "off",
        signal_meta_enabled=(os.getenv("SALES_SIGNAL_INBOUND_META") or "").strip().lower() in {"on", "1", "true"},
        api_key_present=_api_key_present(),
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
        "profile": _profile(),
        "metrics": metrics,
        "readiness": {t: [asdict(c) for c in readiness(t, facts)] for t in _TARGETS},
        "can": {t: list(can_set_mode(t, facts)) for t in _TARGETS},
    }


@router.get("/perception/rollout")
def get_rollout() -> dict[str, Any]:
    return _payload(read_state(_vault_dir()))


def _actor(request: Request) -> str:
    """Quién pidió el cambio, para la auditoría. `require_auth` ya validó el
    token antes de llegar acá (y el cast de Agents lo reenvía): se lee el
    usuario del access-token sin volver a verificarlo."""
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        return "dashboard"
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return "servicio"
    who = claims.get("username") or claims.get("cognito:username") or claims.get("sub") if isinstance(claims, dict) else None
    return str(who)[:120] if who else "dashboard"


def _rank(mode: str) -> int:
    return MODES.index(mode) if mode in MODES else 0


@router.put("/perception/rollout")
def put_rollout(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    current = read_state(_vault_dir())
    mode = str(body.get("mode") or "")
    if mode not in MODES:
        raise HTTPException(422, detail={"reason": "invalid_mode", "message": "Modos: off, shadow, canary u on."})
    raising = _rank(mode) > _rank(current.mode)
    # Lo que viene en el pedido se valida siempre; lo guardado, solo al subir
    # (un valor raro guardado nunca bloquea apagar ni bajar).
    percent = body["canary_percent"] if "canary_percent" in body else current.canary_percent
    if ("canary_percent" in body or raising) and (
        not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100
    ):
        raise HTTPException(422, detail={"reason": "invalid_percent", "message": "El porcentaje va de 0 a 100."})
    numbers = body["test_numbers"] if "test_numbers" in body else list(current.test_numbers)
    if ("test_numbers" in body or raising) and (
        not isinstance(numbers, list) or not all(isinstance(n, str) and _SID_RE.fullmatch(n) for n in numbers)
    ):
        raise HTTPException(422, detail={"reason": "invalid_test_numbers", "message": "Números de prueba como wa_57…"})
    if raising:
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
        # Las métricas tardan: si mientras tanto alguien apagó (o movió el
        # modo), subir no pisa ese cambio.
        if read_state(_vault_dir()) != current:
            raise HTTPException(
                409,
                detail={"reason": "changed", "message": "El estado cambió mientras se revisaba; vuelve a intentarlo."},
            )
    actor = _actor(request)
    state = RolloutState(
        mode=mode,
        canary_percent=percent,
        test_numbers=tuple(numbers),
        updated_at_ms=_now_ms(),
        updated_by=actor,
    )
    write_state(_vault_dir(), state)
    logger.info(
        "perception.rollout_changed",
        previous=current.mode,
        mode=mode,
        canary_percent=percent,
        test_numbers=len(numbers),
        by=actor,
    )
    return _payload(state)
