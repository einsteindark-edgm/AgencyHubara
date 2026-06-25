"""Cliente fino de Conductor (`:6767/api`) — el relay POLL-based del bridge.

Dos capas (port + IO, como el resto): `interpret(workflow)` PURA (deriva el estado lógico
del run del JSON de una ejecución) + `fetch_workflow` IO (urllib). El backend hubara NO
importa GraphAgents — re-implementa el contrato REST estable de Conductor (la misma API que
pinta la UI de `:6767`). Ese es el borde del bridge: HTTP/execution-id, no código compartido.

POLL, no push: AgentSpan es poll-based y una HUMAN task es PASIVA (no corre código que
pueda pushear el `awaiting`) → solo se detecta polleando + leyendo `taskType==HUMAN`.
"""
from __future__ import annotations

import ast
import json
import urllib.parse
import urllib.request
from typing import Any

#: status de Conductor que cuentan como fallo terminal.
_FAILED = {"FAILED", "FAILED_WITH_TERMINAL_ERROR", "TERMINATED", "TIMED_OUT"}


def _unwrap_output(output: Any) -> Any:
    """AgentSpan envuelve el output como `{'result': '<repr Python del state>'}` (L-8) →
    el state real. `ast.literal_eval` es seguro (solo literales). Si no parsea, crudo."""
    if isinstance(output, dict) and list(output) == ["result"] and isinstance(output["result"], str):
        try:
            return ast.literal_eval(output["result"])
        except (ValueError, SyntaxError):
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


# ------------------------------------------------------------------------ IO

def fetch_workflow(execution_id: str, base_url: str, timeout: float = 8) -> dict:
    """El JSON completo (con `tasks[]`) de una ejecución, desde Conductor."""
    eid = urllib.parse.quote(execution_id, safe="")
    root = base_url.rstrip("/").removesuffix("/api")
    url = f"{root}/api/workflow/{eid}?includeTasks=true"
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — URL interna controlada
        return json.loads(r.read().decode("utf-8", "replace"))
