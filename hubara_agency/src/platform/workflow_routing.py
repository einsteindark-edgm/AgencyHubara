"""Enrutamiento del workflow de un agente por conversación.

La plataforma arranca workflows de agentes por NOMBRE (el del manifiesto,
ADR-2026-05-20). Un plugin puede tener más de una versión del mismo agente
corriendo a la vez (ventas V1 y V2, motor de decisiones fase F2/F7): cuál
arranca para una conversación lo decide el plugin, no la plataforma (R-DIP
#9). El plugin registra un enrutador `session_id → nombre | None` para su
worker; la plataforma lo consulta en cada arranque. Sin enrutador, o si el
enrutador falla o no decide, el nombre del manifiesto: arrancar nunca se
frena por esto.

Una conversación VIVA no cambia de versión: los arranques usan el mismo id
(`session-<sid>`), y signal-with-start entrega la señal al workflow que ya
corre, sea cual sea su tipo.
"""
from __future__ import annotations

from collections.abc import Callable

import structlog

logger = structlog.get_logger()

WorkflowRouter = Callable[[str], "str | None"]

_ROUTERS: dict[tuple[str, str], WorkflowRouter] = {}


def register_workflow_router(plugin_id: str, worker: str, router: WorkflowRouter) -> None:
    _ROUTERS[(plugin_id, worker)] = router


def clear_workflow_routers() -> None:
    """Solo para tests."""
    _ROUTERS.clear()


def route_workflow(plugin_id: str, worker: str, default: str, *, session_id: str | None) -> str:
    """El nombre del workflow para esta conversación (o `default`)."""
    router = _ROUTERS.get((plugin_id, worker))
    if router is None or not session_id:
        return default
    try:
        name = router(session_id)
    except Exception as exc:  # noqa: BLE001 — el enrutador nunca frena un arranque
        logger.warning("workflow_routing.router_failed", plugin=plugin_id, worker=worker, error=repr(exc)[:200])
        return default
    return name if isinstance(name, str) and name else default
