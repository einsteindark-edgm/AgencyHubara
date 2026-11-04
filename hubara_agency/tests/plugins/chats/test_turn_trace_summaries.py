"""Calidad LLM sin 504 (2026-11-03): el resumen de las trazas de una sesión.

La lista de conversaciones y el filtro por bot de la matriz y las gráficas
solo necesitan de cada traza su episodio, su turno, quién lo disparó, cuándo
empezó y qué bot respondió. Cada traza trae además las tools con sus
argumentos y extractos (puede pasar los 64 KB): releerlas todas por cada
conversación de 56 días tumbaba la pestaña. El resumen se guarda y una sesión
que no tuvo turnos nuevos no se vuelve a leer.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces

SID = "wa_100000000001"
_HEAVY = {"tools": [{"name": "search_products", "args": {"q": "x" * 500}, "result_excerpt": "r" * 3000}], "reply": "Hola"}


def _turn(episode: str, turn: int, **extra) -> dict:
    return {"episode_id": episode, "turn": turn, "trigger": "customer", "turn_started_ms": 1_000 * turn, **_HEAVY, **extra}


def test_the_summary_keeps_what_the_list_needs_and_not_the_tools(tmp_path: Path) -> None:
    turn_traces.append_trace(tmp_path, SID, _turn("ep_001", 1, workflow="v2"))
    turn_traces.append_trace(tmp_path, SID, _turn("ep_001", 2, trigger="complement"))
    old_jev = _turn("ep_002", 3, egress={"verdicts": [{"capability": "rescate", "value": "Hola"}]})
    turn_traces.append_trace(tmp_path, SID, old_jev)
    turn_traces.append_trace(tmp_path, SID, {k: v for k, v in _turn("ep_003", 4).items() if k != "trigger"})

    summaries = turn_traces.read_summaries(tmp_path, SID)

    assert summaries == [
        {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": 1_000, "workflow": "v2"},
        {"episode_id": "ep_001", "turn": 2, "trigger": "complement", "turn_started_ms": 2_000},
        # Trazas de antes de `workflow`: queda que el workflow nuevo dejó la salida de Jev.
        {"episode_id": "ep_002", "turn": 3, "trigger": "customer", "turn_started_ms": 3_000, "egress": True},
        {"episode_id": "ep_003", "turn": 4, "turn_started_ms": 4_000},
    ]
    full = turn_traces.read_traces(tmp_path, SID)
    for ep in ("ep_001", "ep_002", "ep_003"):
        assert episode_bot(s for s in summaries if s["episode_id"] == ep) == episode_bot(
            t for t in full if t["episode_id"] == ep
        )


def test_a_session_without_new_turns_is_not_read_again(tmp_path: Path) -> None:
    turn_traces.append_trace(tmp_path, SID, _turn("ep_001", 1, workflow="v2"))
    first = turn_traces.read_summaries(tmp_path, SID)
    path = turn_traces.trace_path(tmp_path, SID)
    st = path.stat()
    path.write_bytes(b"x" * (st.st_size - 1) + b"\n")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    assert turn_traces.read_summaries(tmp_path, SID) == first


def test_a_new_turn_shows_up_in_the_summary(tmp_path: Path) -> None:
    turn_traces.append_trace(tmp_path, SID, _turn("ep_001", 1, workflow="v1"))
    assert [s["turn"] for s in turn_traces.read_summaries(tmp_path, SID)] == [1]

    turn_traces.append_trace(tmp_path, SID, _turn("ep_001", 2, workflow="v2"))

    assert [(s["turn"], s["workflow"]) for s in turn_traces.read_summaries(tmp_path, SID)] == [(1, "v1"), (2, "v2")]


def test_a_session_without_traces_has_no_summary(tmp_path: Path) -> None:
    assert turn_traces.read_summaries(tmp_path, SID) == []
    assert not (tmp_path / SID).exists()
