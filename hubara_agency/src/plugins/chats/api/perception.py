"""Contrato `perception-rollout@v1` (plan del laboratorio PR 16): el encendido
del bot nuevo (capas con clasificador) por etapas.

  GET /api/chats/perception/rollout       estado, techo, chequeos por modo, métricas de la sombra,
                                          el resumen de la sonda diaria de Jev (`probe`) y, del
                                          motor de decisiones (F7), `capabilities` y `workflow_v2`
  PUT /api/chats/perception/rollout       {mode, canary_percent?, test_numbers?}
  PUT /api/chats/perception/capabilities  {capability, mode}   una capacidad del motor
  PUT /api/chats/perception/workflow      {mode}               la versión del workflow (off/canary/on)
  GET /api/chats/perception/engine        la versión del motor de decisiones y cada decisión que
                                          toma: dónde actúa, qué resuelve y quién la decide hoy

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

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.capability_rollout import (
    CapabilityFacts,
    can_set_capability,
    can_set_workflow,
    capability_facts,
    workflow_readiness,
)
from src.plugins.chats.agent.sales.decisions.capability_rollout import readiness as capability_readiness
from src.plugins.chats.agent.sales.decisions.probe import read_latest as read_latest_probe
from src.plugins.chats.agent.sales.decisions.rollout import (
    MODES,
    RolloutFacts,
    RolloutState,
    can_set_mode,
    readiness,
)
from src.plugins.chats.agent.sales.decisions.rollout_store import (
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
_WORKFLOW_TARGETS = ("canary", "on")


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


def _probe() -> dict[str, Any]:
    """El resumen de la última sonda diaria de Jev (`decisions/probe.py`);
    `sin_datos` si todavía no corrió."""
    report = read_latest_probe(_vault_dir()) or {}
    at_ms = report.get("at_ms")
    pass_rate = report.get("pass_rate")
    return {
        "status": str(report.get("status") or "sin_datos"),
        "at_ms": at_ms if isinstance(at_ms, int) and not isinstance(at_ms, bool) else None,
        "pass_rate": pass_rate if isinstance(pass_rate, (int, float)) and not isinstance(pass_rate, bool) else None,
        "models": [str(m) for m in report.get("models") or [] if isinstance(m, str)],
    }


def _facts(state: RolloutState) -> tuple[RolloutFacts, dict[str, Any], dict[str, Any]]:
    metrics = _metrics()
    probe = _probe()
    facts = RolloutFacts(
        ceiling=_ceiling(),
        current=state.mode if state.mode in MODES else "off",
        signal_meta_enabled=(os.getenv("SALES_SIGNAL_INBOUND_META") or "").strip().lower() in {"on", "1", "true"},
        api_key_present=_api_key_present(),
        shadow_days=metrics.days,
        shadow_turns=metrics.turns,
        shadow_fallback_rate=metrics.fallback_rate,
        shadow_p95_ms=metrics.p95_ms,
        shadow_model_changed=metrics.model_changed,
        probe_status=probe["status"] if probe["at_ms"] is not None else None,
        probe_age_ms=_now_ms() - probe["at_ms"] if probe["at_ms"] is not None else None,
    )
    return facts, asdict(metrics), probe


def _capability_facts(capability: str, mode: str, ceiling: str) -> CapabilityFacts:
    try:
        return capability_facts(capability, vault_dir=_vault_dir(), now_ms=_now_ms(), ceiling=ceiling, current=mode)
    except Exception as exc:  # noqa: BLE001 — sin métricas cuenta como cero: nadie sube por error
        logger.warning("decisions.capability_facts_failed", capability=capability, error=repr(exc)[:200])
        return CapabilityFacts(ceiling, mode, 0, 0, None, None, 0, 0, 0)


def _capabilities() -> dict[str, Any]:
    """Motor de decisiones (F7): cada capacidad con su modo, el techo de
    Terraform y su vara por modo."""
    ceiling = bots.capabilities_ceiling()
    out: dict[str, Any] = {}
    for capability, mode in bots.capability_modes(_vault_dir()).items():
        facts = _capability_facts(capability, mode, ceiling)
        out[capability] = {
            "mode": mode,
            "ceiling": ceiling,
            "facts": asdict(facts),
            "readiness": {t: [asdict(c) for c in capability_readiness(t, facts)] for t in _TARGETS},
            "can": {t: list(can_set_capability(t, facts)) for t in _TARGETS},
        }
    return out


def _workflow() -> dict[str, Any]:
    """Motor de decisiones (F7): la versión del workflow de ventas (V2 por
    números de prueba y porcentaje antes de todos)."""
    mode, ceiling = bots.workflow_mode(_vault_dir()), bots.workflow_ceiling()
    return {
        "mode": mode,
        "ceiling": ceiling,
        "readiness": {
            t: [asdict(c) for c in workflow_readiness(t, ceiling=ceiling, current=mode)] for t in _WORKFLOW_TARGETS
        },
        "can": {t: list(can_set_workflow(t, ceiling=ceiling, current=mode)) for t in _WORKFLOW_TARGETS},
    }


def _payload(state: RolloutState) -> dict[str, Any]:
    facts, metrics, probe = _facts(state)
    return {
        "state": {**asdict(state), "test_numbers": list(state.test_numbers)},
        "ceiling": facts.ceiling,
        "profile": _profile(),
        "metrics": metrics,
        "probe": probe,
        "readiness": {t: [asdict(c) for c in readiness(t, facts)] for t in _TARGETS},
        "can": {t: list(can_set_mode(t, facts)) for t in _TARGETS},
        "capabilities": _capabilities(),
        "workflow_v2": _workflow(),
    }


@router.get("/perception/rollout")
def get_rollout() -> dict[str, Any]:
    return _payload(read_state(_vault_dir()))


def _effective(mode: str, ceiling: str) -> str:
    """El modo que de verdad corre: el guardado, dentro del techo de Terraform."""
    order = list(MODES)
    mode = mode if mode in order else "off"
    ceiling = ceiling if ceiling in order else "off"
    return order[min(order.index(mode), order.index(ceiling))]


def _turn_summary(bundle: Any) -> dict[str, Any] | None:
    turn = getattr(bundle, "turn", None)
    if turn is None:
        return None
    questionnaire = turn.questionnaire if isinstance(turn.questionnaire, dict) else {}
    return {
        "policy": turn.policy,
        "topics": len(questionnaire.get("topics") or []),
        "questions": len(questionnaire.get("questions") or []),
    }


@router.get("/perception/engine")
def get_engine() -> dict[str, Any]:
    """Calidad LLM → «Motor de decisiones» (2026-10-02): qué versión del motor
    corre la tienda (el paquete de decisión, el oráculo, el perfil del turno)
    y cada decisión que toma, por la parte del software donde actúa, con lo
    que resuelve (`builtins.yaml: about`) y quién la decide hoy (el modo
    guardado dentro del techo; una variante, con el interruptor de su
    decisión)."""
    from src.plugins.chats.agent.sales.decisions import registry
    from src.plugins.chats.shared.store_pack import DEFAULT_BUNDLE
    from src.sdk.decisionkit import ENGINE_CONTRACT, BundleError

    try:
        bundle = registry.active_bundle()
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=f"El paquete de decisión de la tienda no compila: {exc}"[:500]) from None
    modes = bots.capability_modes(_vault_dir())
    ceiling = bots.capabilities_ceiling()
    decisions = []
    for name, table in bundle.capabilities.items():
        about = bundle.about.get(name)
        control = table.control
        decisions.append(
            {
                "capability": name,
                "name": about.name if about else name,
                "where": list(about.where) if about else [],
                "solves": about.solves if about else "",
                "variant_of": control if control != name else None,
                "mode": _effective(modes.get(control, "off"), ceiling),
            }
        )
    return {
        "bundle": {
            "id": bundle.id,
            "version": bundle.version,
            "ref": bundle.ref,
            "oracle": bundle.oracle,
            "engine_contract": ENGINE_CONTRACT,
            "code_default": DEFAULT_BUNDLE,
        },
        "profile": (os.getenv("SALES_PERCEPTION_PROFILE") or bots.DEFAULT_PROFILE).strip(),
        "places": [{"id": place, "label": label} for place, label in bundle.places],
        "decisions": decisions,
        "turn": _turn_summary(bundle),
    }


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
        facts, _, _ = _facts(current)
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


@router.put("/perception/capabilities")
def put_capability(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Mueve UNA capacidad del motor de decisiones (F7) dentro del techo:
    `off`=reglas, `shadow`=sombra, `canary`/`on`=Jev. Bajar siempre pasa;
    subir exige su vara."""
    capability = str(body.get("capability") or "")
    mode = str(body.get("mode") or "")
    if capability not in bots.CAPABILITIES:
        raise HTTPException(
            422, detail={"reason": "unknown_capability", "message": "Capacidades: " + ", ".join(bots.CAPABILITIES) + "."}
        )
    if mode not in MODES:
        raise HTTPException(422, detail={"reason": "invalid_mode", "message": "Modos: off, shadow, canary u on."})
    current = bots.capability_modes(_vault_dir())[capability]
    if _rank(mode) > _rank(current):
        facts = _capability_facts(capability, current, bots.capabilities_ceiling())
        failing = can_set_capability(mode, facts)
        if failing:
            raise HTTPException(
                422,
                detail={
                    "reason": "not_ready",
                    "failing": list(failing),
                    "readiness": [asdict(c) for c in capability_readiness(mode, facts)],
                },
            )
        if bots.capability_modes(_vault_dir())[capability] != current:
            raise HTTPException(
                409,
                detail={"reason": "changed", "message": "El estado cambió mientras se revisaba; vuelve a intentarlo."},
            )
    actor = _actor(request)
    bots.write_capability_modes(_vault_dir(), {capability: mode})
    logger.info("decisions.capability_changed", capability=capability, previous=current, mode=mode, by=actor)
    return _payload(read_state(_vault_dir()))


@router.put("/perception/workflow")
def put_workflow(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Mueve la versión del workflow de ventas (F7): V2 en `canary` actúa en
    los números de prueba y el porcentaje del control; `on`, en todos. Bajar
    siempre pasa (vuelta atrás: el siguiente mensaje arranca V1)."""
    mode = str(body.get("mode") or "")
    if mode not in bots.WORKFLOW_MODES:
        raise HTTPException(422, detail={"reason": "invalid_mode", "message": "Modos del workflow: off, canary u on."})
    current = bots.workflow_mode(_vault_dir())
    failing = can_set_workflow(mode, ceiling=bots.workflow_ceiling(), current=current)
    if failing:
        raise HTTPException(
            422,
            detail={
                "reason": "not_ready",
                "failing": list(failing),
                "readiness": [
                    asdict(c) for c in workflow_readiness(mode, ceiling=bots.workflow_ceiling(), current=current)
                ],
            },
        )
    actor = _actor(request)
    bots.write_workflow_mode(_vault_dir(), mode)
    logger.info("decisions.workflow_changed", previous=current, mode=mode, by=actor)
    return _payload(read_state(_vault_dir()))

