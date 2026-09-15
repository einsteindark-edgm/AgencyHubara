"""Activity `persist_turn_trace` (HU-SC-0) + store de trazas por sesión.

La traza vive en un archivo PROPIO por sesión (`<vault>/<sesión>/evals/
turn_traces.jsonl`), no en el JSONL del dashboard: el corte por episodio del
evaluador cuenta líneas de ese JSONL (`msgs_count_at_start/close`) y el
dashboard pinta cada evento como burbuja.
"""
from __future__ import annotations

import json
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.shared import turn_traces
from src.plugins.chats.agent.sales.activities.turn_trace import (
    persist_turn_trace_activity,
)

SESSION = "wa_570000000001"


def _write_metadata(vault: Path, data: dict) -> None:
    path = vault / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _payload(**over) -> str:
    base = {
        "trigger": "customer",
        "inbound_text": "El primero azulito",
        "turn_started_ms": 1,
        "first_contact": False,
        "tools": [],
        "discarded_narration": [],
        "llm_text": "¿Te lo dejo en azul?",
        "sent_texts": ["¿Te lo dejo en azul?"],
        "suppressed_reason": None,
        "guards": [],
    }
    base.update(over)
    return json.dumps(base)


def test_store_appends_and_reads_back_in_order(tmp_path: Path) -> None:
    turn_traces.append_trace(tmp_path, SESSION, {"turn": 1})
    turn_traces.append_trace(tmp_path, SESSION, {"turn": 2})

    assert [t["turn"] for t in turn_traces.read_traces(tmp_path, SESSION)] == [1, 2]
    assert turn_traces.last_trace(tmp_path, SESSION) == {"turn": 2}
    assert turn_traces.trace_path(tmp_path, SESSION).parent.name == "evals"


def test_store_skips_corrupt_lines_and_missing_file(tmp_path: Path) -> None:
    assert turn_traces.read_traces(tmp_path, SESSION) == []
    assert turn_traces.last_trace(tmp_path, SESSION) is None
    path = turn_traces.trace_path(tmp_path, SESSION)
    path.parent.mkdir(parents=True)
    path.write_text('{"turn": 1}\nno-json\n{"turn": 2}\n', encoding="utf-8")

    assert [t["turn"] for t in turn_traces.read_traces(tmp_path, SESSION)] == [1, 2]


async def test_persist_turn_trace_writes_enriched_record_chained_to_previous(
    _isolate_vault_dir: Path,
) -> None:
    vault = _isolate_vault_dir
    _write_metadata(
        vault,
        {
            "tag": "NO_ETIQUETADO",
            "active_route": "ventas",
            "episodes": [
                {"episode_id": "ep_003", "order_draft": {"slots": {"producto": "cubo-love"}}}
            ],
        },
    )
    env = ActivityEnvironment()

    first = await env.run(persist_turn_trace_activity, SESSION, _payload())
    second = await env.run(persist_turn_trace_activity, SESSION, _payload(trigger="ghost"))

    assert first is True and second is True
    traces = turn_traces.read_traces(vault, SESSION)
    assert [(t["episode_id"], t["turn"]) for t in traces] == [("ep_003", 1), ("ep_003", 2)]
    assert traces[0]["stage_out"] == "variantes"
    assert traces[1]["stage_in"] == "variantes"
    assert traces[1]["trigger"] == "ghost"


async def test_persist_turn_trace_never_raises_on_bad_payload(_isolate_vault_dir: Path) -> None:
    env = ActivityEnvironment()

    assert await env.run(persist_turn_trace_activity, SESSION, "no-json") is False
