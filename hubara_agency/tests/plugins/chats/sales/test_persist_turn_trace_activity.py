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

SESSION = "wa_100000000001"


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


async def test_each_customer_turn_leaves_a_line_for_the_hole_witness(_isolate_vault_dir: Path) -> None:
    """Testigo de huecos (2026-10-07): un turno del cliente deja su línea en
    `_huecos/<día>.jsonl`; un ghosting no (no es un turno del cliente)."""
    vault = _isolate_vault_dir
    _write_metadata(vault, {"episodes": [{"episode_id": "ep_003"}]})
    promise = "Dame un momento y te confirmo"
    env = ActivityEnvironment()

    await env.run(persist_turn_trace_activity, SESSION, _payload(llm_text=promise, sent_texts=[promise]))
    await env.run(persist_turn_trace_activity, SESSION, _payload(trigger="ghost"))

    lines = [json.loads(x) for f in sorted((vault / "_huecos").glob("*.jsonl")) for x in f.read_text().splitlines()]
    assert [(x["episode"], x["turn"], x["huecos"]) for x in lines] == [("ep_003", 1, ["texto_suelto_promete_volver"])]


async def test_persist_turn_trace_never_raises_on_bad_payload(_isolate_vault_dir: Path) -> None:
    env = ActivityEnvironment()

    assert await env.run(persist_turn_trace_activity, SESSION, "no-json") is False


def test_last_trace_reads_a_record_bigger_than_a_small_tail_window(tmp_path: Path) -> None:
    """Un turno con muchas tools produce una línea grande; si la cola leída no
    la cubre entera, `previous` se pierde y la numeración de turnos reinicia."""
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_001", "turn": 1})
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_001", "turn": 2, "llm_text": "x" * 100_000})

    last = turn_traces.last_trace(tmp_path, SESSION)

    assert last is not None and last["turn"] == 2


async def test_the_backup_question_on_unconsulted_claims_runs_in_shadow(_isolate_vault_dir: Path, monkeypatch) -> None:
    """Motor de decisiones (F6): con un bot que la pregunta, la traza guarda
    si el texto enviado afirma algo que solo se sabe consultando (stock,
    entrega) sin haber consultado. Arranca en sombra: nunca actúa, solo se
    mide. Con el bot de hoy no se pregunta nada."""
    from src.platform.perception.adapters.fake import FakePerceptionAdapter
    from src.sdk import connectorkit
    from src.sdk.connectorkit import TypedAnswer

    _write_metadata(_isolate_vault_dir, {})
    claim = _payload(sent_texts=["¡Sí hay stock! Te llega mañana 🤍"], llm_text="¡Sí hay stock! Te llega mañana 🤍")

    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    assert await ActivityEnvironment().run(persist_turn_trace_activity, SESSION, claim) is True
    monkeypatch.setenv("DECISIONS_BOT", "B")
    fake = FakePerceptionAdapter({"afirmacion.sin_consultar": TypedAnswer(id="afirmacion.sin_consultar", kind="noul", p=0.92)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    assert await ActivityEnvironment().run(persist_turn_trace_activity, SESSION, claim) is True

    today, with_jev = turn_traces.read_traces(_isolate_vault_dir, SESSION)
    assert "claims" not in today
    assert with_jev["claims"]["jev"] is True and with_jev["claims"]["capability"] == "afirmacion"


import dataclasses  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("workflow_type", "expected"),
    [("HubaraSalesSessionWorkflowV2", "v2"), ("HubaraSalesSessionWorkflow", "v1"), ("OtroWorkflow", None)],
)
async def test_the_trace_says_which_workflow_answered(_isolate_vault_dir: Path, workflow_type: str, expected) -> None:
    """Calidad LLM separa el bot Jev (el workflow nuevo) del actual: la traza
    lo dice (antes solo se deducía por la salida de Jev, que el V1 no trae)."""
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_type=workflow_type)

    assert await env.run(persist_turn_trace_activity, SESSION, _payload()) is True

    [trace] = turn_traces.read_traces(_isolate_vault_dir, SESSION)
    assert trace.get("workflow") == expected
