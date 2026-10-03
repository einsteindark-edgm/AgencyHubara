"""Chats: el hilo de cada turno del bot (plan del laboratorio PR 17).

  GET /api/chats/sessions/{sesión}/turns                   índice de turnos
  GET /api/chats/sessions/{sesión}/turns/trace?turn_key=   el hilo de UN turno

Se lee de la traza por turno del vault (`<sesión>/evals/turn_traces.jsonl`,
la misma que alimenta el scorecard). El `turn_key` va como query: lleva `/`.
El panel del chat recibe el `turn_key` de cada burbuja en
`/api/dashboard/sessions/{sesión}` y abre el hilo con este endpoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.plugins.chats.api.session_guard import require_valid_session_id
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.turn_view import trace_view, turn_key_of
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _traces(session_id: str) -> list[dict[str, Any]]:
    require_valid_session_id(session_id)
    traces = turn_traces.read_traces(_vault_dir(), session_id)
    # Una traza con un campo raro va al principio: no tumba el índice.
    return sorted(
        (t for t in traces if isinstance(t, dict)),
        key=lambda t: (_number(t.get("turn_started_ms")), _number(t.get("turn"))),
    )


@router.get("/sessions/{session_id}/turns")
def list_turns(session_id: str) -> dict[str, Any]:
    turns = []
    for trace in _traces(session_id):
        steps = trace.get("steps")
        turns.append(
            {
                "turn_key": turn_key_of(trace, session_id),
                "episode_id": trace.get("episode_id"),
                "turn": trace.get("turn"),
                "trigger": trace.get("trigger") or "customer",
                "turn_started_ms": trace.get("turn_started_ms"),
                "mode": trace.get("mode") or "off",
                "fidelity": "v2" if isinstance(steps, list) and steps else "v1",
            }
        )
    return {"turns": turns}


@router.get("/sessions/{session_id}/turns/trace")
def turn_trace(session_id: str, turn_key: str = Query(..., max_length=200)) -> dict[str, Any]:
    for trace in _traces(session_id):
        if turn_key_of(trace, session_id) == turn_key:
            return trace_view(trace)
    raise HTTPException(404, detail="Ese turno no tiene traza.")
