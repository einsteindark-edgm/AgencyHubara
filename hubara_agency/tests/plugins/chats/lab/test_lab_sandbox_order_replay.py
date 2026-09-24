"""El estado del pedido en el sandbox es el que vio producción (premortem C-M2).

`check_order_status` lee el seguimiento de entrega (metadata) y Medusa EN
VIVO. En el sandbox no hay Medusa (`EmptyOrderQuery`) y la metadata del
banco es la del final del día: el bot simulado recibía otro estado que el
real, o uno del futuro, y A1 se apartaba de A0 por el entorno, no por el bot
(baja la fidelidad del simulador, vara 90 %). Ahora:

  * el caso lleva lo que esas tools devolvieron en el turno REAL, leído del
    historial del LLM del banco (el resultado completo; la traza solo guarda
    un extracto de 240 caracteres);
  * en el sandbox, `execute_tool` devuelve eso a la misma tool;
  * sin grabación, corre la tool con el stub y el caso lo anota;
  * el seguimiento de entrega de la metadata queda cortado al inicio del turno.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from temporalio import activity

from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_lab.recorded_tools import REPLAYED_TOOLS, recorded_tool_results
from src.plugins.chats.agent.sales_lab.sandbox.activities import SandboxCapture, sandbox_activities
from src.plugins.chats.agent.sales_lab.sandbox.materialize import metadata_as_of
from tests.plugins.chats.lab.test_lab_cases import SID, T0, WS, _bench

STATUS = json.dumps(
    {"orders": [{"order_id": "order_01TEST", "status": "en camino", "status_code": "shipping",
                 "pay_status": "paid", "payment_confirmed": True, "display_id": "31", "total_cop": 89000,
                 "last_update": "2026-09-20 14:00 UTC", "stages_notified": ["preparing", "shipping"]}]},
    ensure_ascii=False,
)
LATER = json.dumps({"orders": [{"order_id": "order_01TEST", "status": "entregado", "status_code": "delivered"}]})


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _call(call_id: str, name: str, args: dict | None = None) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


def _llm_lines() -> list[dict]:
    """Historial del LLM: turno 1 (saludo), turno 2 (consulta el pedido y
    busca productos) y un turno siguiente que vuelve a consultar."""
    return [
        {"_type": "metadata", "key": SID, "last_consolidated": 0, "metadata": {}},
        {"role": "user", "content": "hola", "timestamp": _iso(T0 + 8_000)},
        {"role": "assistant", "content": "¡Buenas tardes!", "timestamp": _iso(T0 + 8_000)},
        {"role": "user", "content": "me mandas el catálogo\ny el envío a Bogotá", "timestamp": _iso(T0 + 74_000)},
        {"role": "assistant", "content": "", "tool_calls": [_call("c1", "check_order_status"), _call("c2", "search_products", {"q": "vela"})],
         "timestamp": _iso(T0 + 74_000)},
        {"role": "tool", "tool_call_id": "c1", "name": "check_order_status", "content": STATUS, "timestamp": _iso(T0 + 74_000)},
        {"role": "tool", "tool_call_id": "c2", "name": "search_products", "content": '{"count": 3}', "timestamp": _iso(T0 + 74_000)},
        {"role": "assistant", "content": "Tu pedido va en camino", "timestamp": _iso(T0 + 74_000)},
        {"role": "user", "content": "gracias", "timestamp": _iso(T0 + 3_800_000)},
        {"role": "assistant", "content": "", "tool_calls": [_call("c3", "check_order_status")], "timestamp": _iso(T0 + 3_800_000)},
        {"role": "tool", "tool_call_id": "c3", "name": "check_order_status", "content": LATER, "timestamp": _iso(T0 + 3_800_000)},
    ]


def test_only_the_tools_that_read_live_order_state_are_replayed() -> None:
    assert REPLAYED_TOOLS == frozenset({"check_order_status"})


def test_the_real_turn_results_come_from_the_llm_history_of_that_turn_only() -> None:
    recorded = recorded_tool_results(_llm_lines(), 2)  # el turno 2 empieza en el mensaje 3 (índice 2)

    assert recorded == [{"name": "check_order_status", "args": {}, "content": STATUS}]


def test_a_turn_that_did_not_ask_for_the_order_has_nothing_to_replay() -> None:
    assert recorded_tool_results(_llm_lines(), 0) == []


def test_each_case_carries_what_its_real_turn_got(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    path = bench / "agent_state" / WS / "sessions" / f"{SID}.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in _llm_lines()) + "\n", encoding="utf-8")

    cases = {c.turn: c for c in build_cases(bench, sales_workspace=WS).cases}

    assert cases[1].recorded_tools == []
    assert cases[2].recorded_tools == [{"name": "check_order_status", "args": {}, "content": STATUS}]


# ── en el sandbox: `execute_tool` devuelve lo grabado ──────────────────────


class _Input:
    def __init__(self, name: str) -> None:
        self.name = name
        self.params: dict = {}


def _sandbox_execute_tool(recorded: list[dict], capture: SandboxCapture):
    calls: list[str] = []

    @activity.defn(name="execute_tool")
    async def real_execute_tool(input) -> str:
        calls.append(input.name)
        return '{"orders": [], "note": "stub"}'

    [act] = sandbox_activities([real_execute_tool], capture=capture, recorded_tools=recorded)
    return act, calls


@pytest.mark.asyncio
async def test_the_sandbox_gives_the_simulated_bot_the_state_production_saw() -> None:
    capture = SandboxCapture()
    act, calls = _sandbox_execute_tool([{"name": "check_order_status", "args": {}, "content": STATUS}], capture)

    out = await act(_Input("check_order_status"))

    assert out == STATUS and calls == []
    assert capture.tool_replay == {"replayed": ["check_order_status"], "unrecorded": []}


@pytest.mark.asyncio
async def test_other_tools_run_for_real() -> None:
    capture = SandboxCapture()
    act, calls = _sandbox_execute_tool([{"name": "check_order_status", "args": {}, "content": STATUS}], capture)

    await act(_Input("search_products"))

    assert calls == ["search_products"]
    assert capture.tool_replay == {"replayed": [], "unrecorded": []}


@pytest.mark.asyncio
async def test_without_a_recording_the_stub_answers_and_the_case_says_so() -> None:
    capture = SandboxCapture()
    act, calls = _sandbox_execute_tool([], capture)

    out = await act(_Input("check_order_status"))

    assert calls == ["check_order_status"] and "stub" in out
    assert capture.tool_replay == {"replayed": [], "unrecorded": ["check_order_status"]}


@pytest.mark.asyncio
async def test_a_second_call_in_the_same_turn_gets_the_next_recording_or_the_last_one() -> None:
    capture = SandboxCapture()
    first = {"name": "check_order_status", "args": {}, "content": STATUS}
    act, _ = _sandbox_execute_tool([first], capture)

    assert await act(_Input("check_order_status")) == STATUS
    assert await act(_Input("check_order_status")) == STATUS  # el estado no cambió dentro del turno


# ── el seguimiento de entrega de la metadata, al inicio del turno ──────────


def test_delivery_tracking_is_cut_to_the_turn_start() -> None:
    at = T0 + 100_000
    tracking = {
        "orders": {
            "order_01TEST": {
                "order_id": "order_01TEST",
                "current_stage": "delivered",
                "notified_stages": ["preparing", "shipping", "delivered"],
                "events": [
                    {"stage": "preparing", "at_ms": T0 + 10_000},
                    {"stage": "shipping", "at_ms": T0 + 50_000},
                    {"stage": "delivered", "at_ms": T0 + 900_000},
                ],
                "started_at_ms": T0 + 5_000,
            },
            "order_02LATER": {"order_id": "order_02LATER", "current_stage": "preparing", "events": [],
                              "notified_stages": [], "started_at_ms": T0 + 500_000},
        }
    }
    case = {"case_id": "c", "session_id": SID, "episode_id": "ep_001", "at_ms": at, "episodes_at": [],
            "state_before": {"tag": "INTERESADO", "route": "ventas", "order_id": "order_01TEST"}}

    meta = metadata_as_of({"eta_tracking": tracking, "episodes": []}, case, sales_workspace_path="/ws")

    orders = meta["eta_tracking"]["orders"]
    assert set(orders) == {"order_01TEST"}  # el seguimiento del segundo pedido empezó después
    first = orders["order_01TEST"]
    assert (first["current_stage"], first["notified_stages"]) == ("shipping", ["preparing", "shipping"])
    assert [e["stage"] for e in first["events"]] == ["preparing", "shipping"]


# ── de punta a punta: un turno real del banco en su proceso (como en la caja) ─


def _probe_case(**over) -> dict:
    from tests.plugins.chats.lab.test_lab_sandbox_materialize import _case

    return _case(**over)


def _tool_contents(report: dict) -> list[str]:
    return [m.get("content") for m in report["tool_messages"] if m.get("name") == "check_order_status"]


def test_the_simulated_bot_receives_the_order_state_production_saw(tmp_path: Path) -> None:
    from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe

    case = _probe_case(recorded_tools=[{"name": "check_order_status", "args": {}, "content": STATUS}])

    report = _run_probe(tmp_path, case, tool="check_order_status")

    result = report["result"]
    assert result["error"] is None, result
    assert _tool_contents(report) == [STATUS]
    assert result["tool_replay"] == {"replayed": ["check_order_status"], "unrecorded": []}


def test_without_a_recording_the_case_notes_that_the_stub_answered(tmp_path: Path) -> None:
    from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe

    report = _run_probe(tmp_path, _probe_case(), tool="check_order_status")

    result = report["result"]
    assert result["error"] is None, result
    [content] = _tool_contents(report)
    assert content != STATUS and "orders" in content
    assert result["tool_replay"] == {"replayed": [], "unrecorded": ["check_order_status"]}


def test_the_replayed_state_never_carries_the_real_number(tmp_path: Path) -> None:
    from tests.plugins.chats.lab.test_lab_sandbox_leaks import _run_probe

    with_number = json.dumps({"orders": [], "note": f"pedido de {SID} y del 3001234567"})
    case = _probe_case(recorded_tools=[{"name": "check_order_status", "args": {}, "content": with_number}])

    report = _run_probe(tmp_path, case, tool="check_order_status")

    [content] = _tool_contents(report)
    assert SID.removeprefix("wa_") not in content and "3001234567" not in content


# ── la corrida publica qué turnos recibieron el estado real ────────────────


def _published_row(tmp_path: Path, replay: dict | None) -> dict:
    from src.plugins.chats.agent.sales_lab.run.publish import publish_arm
    from src.sdk.labkit import FilesystemLabStore

    sim = "wa_573009990000"
    store = FilesystemLabStore(tmp_path / "bucket")
    case = {"case_id": f"{SID}/ep_001/t2", "session_id": SID, "episode_id": "ep_001", "turn": 2, "turn_key": "run:abc/t:2"}
    result = {"trace": {"session_id": sim, "turn": 1, "sent_texts": ["Tu pedido va en camino"]}, "sim_session_id": sim}
    if replay is not None:
        result["tool_replay"] = replay
    publish_arm(store, run_id="run-x", arm="A1", rep=1, cases=[case], results={0: result})
    [row] = [json.loads(line) for line in store.get_bytes(f"runs/run-x/turns/A1/1/{SID}.jsonl").decode().splitlines() if line]
    return row


def test_the_published_turn_says_where_its_order_state_came_from(tmp_path: Path) -> None:
    row = _published_row(tmp_path, {"replayed": [], "unrecorded": ["check_order_status"]})

    assert row["tool_replay"] == {"replayed": [], "unrecorded": ["check_order_status"]}


def test_a_turn_that_did_not_read_the_order_publishes_no_note(tmp_path: Path) -> None:
    assert "tool_replay" not in _published_row(tmp_path, {"replayed": [], "unrecorded": []})
    assert "tool_replay" not in _published_row(tmp_path / "otro", None)
