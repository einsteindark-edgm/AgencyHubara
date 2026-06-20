"""Lectura del TRACE de runtime — anota el `execution_plan` con el estado VIVO de cada
nodo, leído de la **REST API de Conductor** (el server de AgentSpan en `:6767/api`),
NO de Postgres crudo: el dato está persistido en Conductor, pero la API es el contrato
estable (la misma que pinta la UI de `:6767`); el esquema interno de la DB no lo es.

Dos capas, como el resto del SDK (port + vendors / pura + IO):
- `build_trace(plan, workflow)` — **PURA**: dado el `execution_plan` de un supervisor +
  el JSON de una ejecución de Conductor (workflow con `tasks[]`), mapea cada task a su
  paso (por el naming `<wf>_<nodo>_<i>` que emite el loader) y lo anota con
  `{status, retries, ms, task_id}`. Testeable con un fixture, sin red.
- `fetch_workflow` / `fetch_runs` / `fetch_node_state` — **IO** fino (urllib) contra
  `:6767/api`. AgentSpan/Conductor es poll-based (no hay SSE): el explorer pollea.

El `status` de Conductor se mapea al vocabulario del explorer: done · running · pending
· failed. Un paso sin task todavía = `pending` (aún no scheduleó). El acc pesado (≈100 KB
por L-14) NO viaja en el trace: se trae lazy por `fetch_node_state` al click en un nodo.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

# Conductor task status -> vocabulario del explorer
_DONE = {"COMPLETED", "COMPLETED_WITH_ERRORS"}
_RUNNING = {"IN_PROGRESS"}
_PENDING = {"SCHEDULED"}
_FAILED = {"FAILED", "FAILED_WITH_TERMINAL_ERROR", "TIMED_OUT", "CANCELED"}


def _map_status(s: str | None) -> str:
    if s in _DONE:
        return "done"
    if s in _RUNNING:
        return "running"
    if s in _PENDING:
        return "pending"
    if s in _FAILED:
        return "failed"
    return "other"


def _dur_ms(task: dict) -> int | None:
    s, e = task.get("startTime"), task.get("endTime")
    if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e > s:
        return int(e - s)
    return None


def _terminal_attempt(cands: list[dict]) -> dict:
    """Entre tasks homónimos (Conductor escribe UN registro por intento de retry), el intento
    FINAL: mayor `retryCount`, desempata por `startTime`. Así un nodo que falló-y-se-recuperó
    reporta su resultado terminal (done/retries=N), no el 1er intento fallido."""
    return max(cands, key=lambda t: ((t.get("retryCount") or 0), t.get("startTime") or 0))


def build_trace(plan: dict, workflow: dict) -> dict:
    """Anota cada paso del `plan` con su task de Conductor. El loader nombra el nodo i
    `<nodo_underscore>_<i>` y Conductor le antepone el workflow → `<wf>_<nodo>_<i>`; el join
    parallel es `<wf>_join`. Matcheamos ANCLADO al prefijo del workflow (`<wf>_…`) — eso evita
    pescar las system-tasks `_fork`/`_join` del FORK_JOIN de Conductor (terminan en `join` pero
    sin el prefijo) y los falsos positivos por sufijo. Entre intentos de retry homónimos se toma
    el terminal. Un paso sin task = `pending`. El router (1 task `<wf>_router`) no matchea sus
    branches → quedan pending y el task va a `unmatched_tasks`."""
    wf = workflow.get("workflowName") or ""
    tasks: list[dict] = workflow.get("tasks", []) or []
    matched: set[int] = set()

    def _claim(suffix: str) -> dict | None:
        anchored = f"{wf}_{suffix}" if wf else suffix
        cands = [t for t in tasks if str(t.get("referenceTaskName", "")).endswith(anchored)]
        if not cands:
            return None
        for t in cands:
            matched.add(id(t))  # marca TODOS los intentos (no ensucian unmatched)
        return _terminal_attempt(cands)

    steps_out: list[dict] = []
    node_i = 0  # índice de NODO del loader (enumera solo los pasos-agente; el join no cuenta)
    for step in plan.get("steps", []):
        task = None
        if step.get("agent") is None and step.get("role") == "join":
            task = _claim("join")
        elif step.get("agent") is not None:
            task = _claim(f"{step['agent'].replace('-', '_')}_{node_i}")
            node_i += 1

        if task is not None:
            runtime = {
                "status": _map_status(task.get("status")),
                "retries": task.get("retryCount") or 0,
                "ms": _dur_ms(task),
                "task_id": task.get("taskId"),
            }
        else:
            runtime = {"status": "pending", "retries": 0, "ms": None, "task_id": None}
        steps_out.append({**step, "runtime": runtime})

    unmatched = [
        {"referenceTaskName": t.get("referenceTaskName"), "status": _map_status(t.get("status"))}
        for t in tasks if id(t) not in matched
    ]
    return {
        "execution_id": workflow.get("workflowId"),
        "workflow_status": workflow.get("status"),
        "strategy": plan.get("strategy"),
        "steps": steps_out,
        "unmatched_tasks": unmatched,
    }


# ------------------------------------------------------------------------ IO

def _base(server_url: str | None = None) -> str:
    url = server_url or os.environ.get("AGENTSPAN_SERVER_URL", "http://localhost:6767")
    return url.rstrip("/").removesuffix("/api")


def _get(url: str, timeout: float = 8) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_workflow(execution_id: str, server_url: str | None = None, timeout: float = 8) -> dict:
    """El JSON completo de una ejecución (con `tasks[]`) desde Conductor."""
    eid = urllib.parse.quote(execution_id, safe="")
    return _get(f"{_base(server_url)}/api/workflow/{eid}?includeTasks=true", timeout)


def fetch_runs(server_url: str | None = None, limit: int = 25, timeout: float = 8) -> list[dict]:
    """Las ejecuciones recientes (el equivalente a los 'threads' de Studio)."""
    q = urllib.parse.urlencode({"size": limit, "sort": "startTime:DESC"})
    data = _get(f"{_base(server_url)}/api/workflow/search?{q}", timeout)
    return [
        {"execution_id": x.get("workflowId"), "agent": x.get("workflowType"),
         "status": x.get("status"), "startTime": x.get("startTime")}
        for x in data.get("results", [])
    ]


def fetch_node_state(execution_id: str, task_id: str, server_url: str | None = None, timeout: float = 8) -> Any:
    """El `acc` (estado acumulador) DESPUÉS de un nodo — lazy, al click. Re-trae el
    workflow y extrae el `outputData.state.acc` del task pedido (el 'ver por dentro')."""
    wf = fetch_workflow(execution_id, server_url, timeout)
    for t in wf.get("tasks", []):
        if t.get("taskId") == task_id:
            return (t.get("outputData") or {}).get("state", {}).get("acc")
    return None
