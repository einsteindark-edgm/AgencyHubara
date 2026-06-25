"""Run-record del buzón de análisis (plugin ads) — el espejo FINO del estado de un run, en el vault.

`status` + log de eventos (append-only, idempotente por `event_id`) + `result` + `awaiting`
(el contexto HITL). NO es la fuente de verdad (esa es AgentSpan/Conductor) — es la vista
que el SSE relaya al dashboard y que sobrevive un refresh del browser o un restart del
backend. El backend la actualiza polleando Conductor; el vault la persiste (escritura
atómica). El vocabulario de eventos es el contrato del bridge (run.started /
run.awaiting_approval / run.resumed / run.result / run.failed).

`WORKSPACE_VAULT_DIR` se importa al top a propósito: el fixture autouse `_isolate_vault_dir`
lo re-bindea a un tmp por test (defensa en profundidad).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.sdk.runtime import WORKSPACE_VAULT_DIR

#: tipo de evento del bridge → status del run-record.
_STATUS = {
    "run.started": "running",
    "run.awaiting_approval": "awaiting_approval",
    "run.resumed": "running",
    "run.result": "completed",
    "run.failed": "failed",
}


def _record_path(run_id: str) -> Path:
    return Path(WORKSPACE_VAULT_DIR) / "ad-analysis" / run_id / "record.json"


def _write(record: dict) -> None:
    p = _record_path(record["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # rename atómico → un lector nunca ve un record a medias


def create_run(run_id: str, *, agent: str, input: dict) -> dict:
    """Crea el record de un run nuevo (status `pending`, sin eventos)."""
    record = {
        "run_id": run_id,
        "agent": agent,
        "input": input,
        "status": "pending",
        "events": [],
        "result": None,
        "awaiting": None,
    }
    _write(record)
    return record


def read_run(run_id: str) -> dict | None:
    """El record actual, o `None` si no existe."""
    p = _record_path(run_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def append_event(run_id: str, event: dict) -> dict:
    """Appendea un evento (idempotente por `event_id`), deriva el status y captura
    `awaiting`/`result`. Falla LOUD (`KeyError`) si el run no existe."""
    record = read_run(run_id)
    if record is None:
        raise KeyError(f"run '{run_id}' no existe")

    eid = event.get("event_id")
    if eid is not None and any(e.get("event_id") == eid for e in record["events"]):
        return record  # ya visto: no duplica (idempotencia del relay)

    record["events"].append(event)
    etype = event.get("type")
    payload: dict[str, Any] = event.get("payload") or {}
    if etype in _STATUS:
        record["status"] = _STATUS[etype]
    if etype == "run.started" and payload.get("execution_id"):
        record["execution_id"] = payload["execution_id"]  # el id de Conductor, para pollear
    elif etype == "run.awaiting_approval":
        record["awaiting"] = payload.get("context")  # lo que el humano necesita decidir
    elif etype == "run.resumed":
        record["awaiting"] = None  # ya resuelto
    elif etype == "run.result":
        record["result"] = payload.get("output")
    elif etype == "run.failed":
        record["error"] = payload.get("error")  # hoist del error al record → la UI lo muestra

    _write(record)
    return record
