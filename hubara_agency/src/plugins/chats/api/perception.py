"""Contrato `perception-rollout@v1` (plan del laboratorio PR 16): el encendido
del bot nuevo (capas con clasificador) por etapas.

  GET /api/chats/perception/rollout       estado, techo, chequeos por modo, métricas de la sombra,
                                          el resumen de la sonda diaria de Jev (`probe`) y, del
                                          motor de decisiones (F7), `capabilities`, `workflow_v2`
                                          y `test_numbers_jev`
  PUT /api/chats/perception/rollout       403 `by_command` (ver abajo)
  PUT /api/chats/perception/capabilities  403 `by_command`
  PUT /api/chats/perception/workflow      403 `by_command`
  GET /api/chats/perception/engine        la versión del motor de decisiones y cada decisión que
                                          toma: dónde actúa, qué resuelve y quién la decide hoy

El panel de la sección Agents (`agents_admin`) lo consume por cast, SOLO para
mostrar. Desde el 2026-10-06 el bot nuevo se cambia únicamente por comando
(decisión del operador: «que los botones de la UI no sirvan y todo se haga
por comandos, para evitar que alguien jugando dañe producción»): los PUT
responden 403 con el comando, y las garantías de cada cambio (techo de
Terraform, la vara al subir, apagar siempre pasa, la firma) viven en
`chats/agent/sales/decisions/control.py`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions import control as bot_control
from src.plugins.chats.agent.sales.decisions.rollout import MODES
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()
logger = structlog.get_logger()

#: El comando que reemplaza a los PUT (dentro del contenedor de la API; desde
#: una máquina del equipo, `infra/scripts/bot_control.sh` lo corre por SSM).
COMMAND = "python -m src.plugins.chats.agent.sales.decisions.control"


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


@router.get("/perception/rollout")
def get_rollout() -> dict[str, Any]:
    return bot_control.snapshot(_vault_dir())


def _by_command(example: str) -> HTTPException:
    return HTTPException(
        403,
        detail={
            "reason": "by_command",
            "message": "El bot nuevo se cambia solo por comando, no desde el dashboard.",
            "command": f"{COMMAND} {example}",
        },
    )


@router.put("/perception/rollout")
def put_rollout() -> dict[str, Any]:
    raise _by_command("--por <quien> percepcion|numeros|porcentaje …")


@router.put("/perception/capabilities")
def put_capability() -> dict[str, Any]:
    raise _by_command("--por <quien> capacidad <nombre> <modo>")


@router.put("/perception/workflow")
def put_workflow() -> dict[str, Any]:
    raise _by_command("--por <quien> workflow <modo>")


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
    from src.plugins.chats.shared.operator import decisions as operator
    from src.plugins.chats.shared.store_pack import DEFAULT_BUNDLE
    from src.sdk.decisionkit import ENGINE_CONTRACT, BundleError

    try:
        bundle = registry.active_bundle()
    except BundleError as exc:
        raise HTTPException(status_code=503, detail=f"El paquete de decisión de la tienda no compila: {exc}"[:500]) from None
    # Los paquetes que corren en el motor: el de la tienda y el de la App Operador.
    bundles = [(bundle, "La tienda: el bot de ventas y remarketing")]
    try:
        bundles.append((operator.active_bundle(), "App Operador: el chat y los incendios del teléfono"))
    except BundleError as exc:
        logger.error("decisions.operator_bundle_broken", error=str(exc)[:300])
    modes = bots.capability_modes(_vault_dir())
    ceiling = bots.capabilities_ceiling()
    places: list[dict[str, str]] = []
    decisions = []
    for each, _label in bundles:
        places += [{"id": place, "label": label} for place, label in each.places]
        for name, table in each.capabilities.items():
            about = each.about.get(name)
            control = table.control
            decisions.append(
                {
                    "capability": name,
                    "name": about.name if about else name,
                    "where": list(about.where) if about else [],
                    "solves": about.solves if about else "",
                    "variant_of": control if control != name else None,
                    "mode": _effective(modes.get(control, "off"), ceiling),
                    "bundle": each.ref,
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
        "bundles": [
            {"id": each.id, "version": each.version, "ref": each.ref, "oracle": each.oracle, "name": label}
            for each, label in bundles
        ],
        "profile": (os.getenv("SALES_PERCEPTION_PROFILE") or bots.DEFAULT_PROFILE).strip(),
        "places": places,
        "decisions": decisions,
        "turn": _turn_summary(bundle),
    }
