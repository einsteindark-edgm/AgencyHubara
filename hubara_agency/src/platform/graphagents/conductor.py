"""Interpretación del estado de Conductor — la capa PURA del relay POLL-based del bridge.

`interpret(workflow)` deriva el estado lógico del run (running / awaiting_approval / completed /
failed) del JSON crudo de una ejecución de Conductor. El backend hubara NO importa GraphAgents:
re-implementa el contrato del JSON de Conductor (la misma forma que pinta la UI de `:6767`).

El FETCH del workflow NO vive acá: lo hace el `Launcher` por SSM (`fetch_status`, corriendo
`sdk.cli status` DENTRO de la caja) — el backend nunca se conecta directo a la caja. Acá solo la
lógica pura, replayeable con un fixture.

POLL, no push: AgentSpan es poll-based y una HUMAN task es PASIVA (no corre código que pueda
pushear el `awaiting`) → solo se detecta polleando + leyendo `taskType==HUMAN`.
"""
from __future__ import annotations

import ast
import json
from typing import Any

#: status de Conductor que cuentan como fallo terminal.
_FAILED = {"FAILED", "FAILED_WITH_TERMINAL_ERROR", "TERMINATED", "TIMED_OUT"}


def _unwrap_output(output: Any) -> Any:
    """AgentSpan envuelve el output como `{'result': '<state serializado>'}` (L-8) →
    el state real. La serialización varió entre runs: repr Python (comillas
    simples, True/None) Y json (true/null — es lo que documenta el runtime de
    la caja, sdk/runtime.py::_unwrap). `ast.literal_eval` primero (seguro),
    json como fallback (PM-001: un state con `true` no es literal Python y el
    consumer veía cero dispatch con run completed). Si nada parsea, crudo."""
    if isinstance(output, dict) and list(output) == ["result"] and isinstance(output["result"], str):
        raw = output["result"]
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            pass
        try:
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return output
    return output


def _human_context(task: dict) -> Any:
    """El contexto que ve el humano — la HUMAN task lo lleva en su `inputData`."""
    return (task.get("inputData") or {}).get("context")


def interpret(workflow: dict) -> dict:
    """JSON de una ejecución de Conductor → estado lógico del run para el record/SSE.

    Una HUMAN task `IN_PROGRESS` = pausa HITL (`awaiting_approval`, con su contexto); el
    `status` del workflow para el resto (running / completed+output / failed+razón).
    """
    tasks = workflow.get("tasks") or []
    human = next(
        (t for t in tasks if t.get("taskType") == "HUMAN" and t.get("status") == "IN_PROGRESS"),
        None,
    )
    if human is not None:
        return {"status": "awaiting_approval", "awaiting": _human_context(human), "result": None, "error": None}

    status = workflow.get("status")
    if status == "COMPLETED":
        return {"status": "completed", "awaiting": None, "result": _unwrap_output(workflow.get("output")), "error": None}
    if status in _FAILED:
        return {"status": "failed", "awaiting": None, "result": None, "error": workflow.get("reasonForIncompletion")}
    return {"status": "running", "awaiting": None, "result": None, "error": None}
