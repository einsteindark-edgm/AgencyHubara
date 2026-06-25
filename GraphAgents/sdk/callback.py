"""Emisor de callbacks del puente — GraphAgents → backend (hubara).

Dos capas (como el resto del SDK: pura + IO / port + vendors):
- `to_event(execution, *, run_id, seq)` — PURO (G-DET): mapea una `Execution` del
  runtime a un evento del protocolo, con `event_id` DETERMINISTA (`<run_id>:<seq>`),
  la llave de idempotencia del consumidor. Sin red, sin time/random (L-1).
- el ENVÍO real (HTTP POST a la `callback_url` + retry) es un sink/vendor aparte
  (G-PORT) — esta capa no toca la red.

Vocabulario de eventos (el contrato que consume el buzón en hubara):
`run.started` · `run.awaiting_approval` · `run.result` · `run.failed`.
"""
from __future__ import annotations

from sdk.runtime import Execution

#: Estado de la Execution → tipo de evento del protocolo del puente.
_TYPE = {
    "running": "run.started",
    "paused": "run.awaiting_approval",
    "completed": "run.result",
    "failed": "run.failed",
}


def _payload(ex: Execution) -> dict:
    """El payload que el humano/consumidor necesita según el estado."""
    if ex.status == "paused":
        return {"context": ex.awaiting}  # qué decide el humano
    if ex.status == "completed":
        return {"output": ex.output}
    if ex.status == "failed":
        return {"error": ex.error}
    return {}


def to_event(execution: Execution, *, run_id: str, seq: int) -> dict:
    """Mapea una `Execution` a un evento del protocolo. PURO + determinista: mismo
    `(run_id, seq)` → mismo `event_id` (idempotencia, sin time/random)."""
    return {
        "run_id": run_id,
        "execution_id": execution.id,
        "event_id": f"{run_id}:{seq}",
        "type": _TYPE[execution.status],
        "payload": _payload(execution),
    }
