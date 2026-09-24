"""El bot actual simulado (A1) corre sobre todo el banco (plan §3.4 y PR 13).

Cada caso × repetición corre en su sandbox (un proceso por caso) y el
resultado se publica en `runs/<corrida>/` con la identidad del caso REAL:
la traza del turno simulado en `turns/A1/<rep>/<sesión>.jsonl` (misma sesión,
episodio, turno y `turn_key` que el turno real, para que el hilo del
laboratorio la encuentre) y la salida en el hilo (`outputs.A1`). El gasto
real de cada caso se suma y la corrida corta al llegar al tope. Los bots
nuevos (B = Jev, C = OpenAI, PR 15) corren igual, con su brazo en el caso;
cada brazo publica sus métricas (`metrics/<brazo>/<rep>.json`) y el
complemento viaja con su caso.
"""
from __future__ import annotations

import json

import pytest

from src.plugins.chats.agent.sales_lab.run.workflow import ARMS_PENDING_NOTE, SPEND_CAP_NOTE
from tests.plugins.chats.lab.test_lab_cases import SID
from tests.plugins.chats.lab.test_lab_run_box import RUN, _run, box  # noqa: F401  (fixture)

SIM = "wa_573009876543"


def _fake_result(case: dict, *, cost: float = 0.01, error: str | None = None) -> dict:
    return {
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "sim_session_id": SIM,
        "turn_key": case["turn_key"],
        "error": error,
        "cost_usd": cost,
        "trace": None if error else {
            "session_id": SIM, "episode_id": "ep_001", "turn": 1, "turn_key": "run:sim/t:1",
            "inbound_text": "hola", "sent_texts": [f"simulado {case['turn']}"], "tools": [], "guards": [],
            "llm_text": f"simulado {case['turn']}",
        },
    }


@pytest.fixture
def sims(box, monkeypatch):  # noqa: F811
    from src.plugins.chats.agent.sales_lab.run import activities as run_acts

    state: dict = {"calls": [], "cost": 0.01}

    async def fake_case(case, *, bench_dir, sandbox_dir, timeout_s, arm="A1"):
        state["calls"].append((case["case_id"], sandbox_dir.parts[-3:], arm))
        result = _fake_result(case, cost=state["cost"])
        return state["decorate"](result, arm) if state.get("decorate") else result

    monkeypatch.setattr(run_acts, "run_case_in_subprocess", fake_case)
    return state


@pytest.mark.asyncio
async def test_a1_runs_every_case_of_every_rep(box, sims) -> None:  # noqa: F811
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, "arms": ["A1"], "reps": 3}).encode())

    result = await _run(box)

    assert result["phase"] == "done"
    simulated = [c for c in sims["calls"] if c[1][0] == "A1"]
    assert len(simulated) == 2 * 3  # 2 casos × 3 repeticiones (+ el turno de humo aparte)
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert (progress["turns_done"], progress["turns_total"]) == (6, 6)
    assert progress["spent_usd"] == pytest.approx(0.07)  # 6 casos + el turno de humo


@pytest.mark.asyncio
async def test_each_simulated_trace_is_published_with_the_real_case_identity(box, sims) -> None:  # noqa: F811
    await _run(box)

    raw = box["store"].get_bytes(f"runs/{RUN}/turns/A1/0/{SID}.jsonl").decode()
    traces = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert [(t["session_id"], t["episode_id"], t["turn"]) for t in traces] == [(SID, "ep_001", 1), (SID, "ep_001", 2)]
    assert traces[1]["turn_key"] == "run:abc/t:2"
    assert traces[0]["source"] == f"lab:{RUN}:A1:0"
    assert SIM not in raw


@pytest.mark.asyncio
async def test_the_thread_carries_the_simulated_output_next_to_the_real_one(box, sims) -> None:  # noqa: F811
    await _run(box)

    thread = json.loads(box["store"].get_bytes(f"runs/{RUN}/threads/{SID}.json"))
    turn2 = next(t for t in thread["turns"] if t["turn"] == 2)
    assert turn2["outputs"]["A1"]["sent_texts"] == ["simulado 2"]
    assert turn2["outputs"]["A0"]["sent_texts"] == ["Claro, el envío sale en 12.900"]


@pytest.mark.asyncio
async def test_the_run_stops_at_the_spend_cap(box, sims) -> None:  # noqa: F811
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, "arms": ["A1"], "reps": 3, "spend_limit_usd": 1.0}).encode())
    sims["cost"] = 0.6

    result = await _run(box)

    assert result["phase"] == "done"
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["turns_done"] < progress["turns_total"]
    assert SPEND_CAP_NOTE in progress["notes"]


@pytest.mark.asyncio
async def test_the_new_bot_runs_every_case_with_its_arm(box, sims) -> None:  # noqa: F811
    await _run(box)  # la orden del fixture pide A1 y B

    b_calls = [c for c in sims["calls"] if c[1][0] == "B"]
    assert len(b_calls) == 2 and {c[2] for c in b_calls} == {"B"}
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["notes"] == [] and (progress["turns_done"], progress["turns_total"]) == (4, 4)


@pytest.mark.asyncio
async def test_an_unknown_arm_is_reported_and_not_run(box, sims) -> None:  # noqa: F811
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, "arms": ["A1", "Z"]}).encode())

    await _run(box)

    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert ARMS_PENDING_NOTE.format(arms="Z") in progress["notes"]
    assert not [c for c in sims["calls"] if c[1][0] == "Z"]


@pytest.mark.asyncio
async def test_each_arm_publishes_its_metrics(box, sims) -> None:  # noqa: F811
    def with_layers(result: dict, arm: str) -> dict:
        if arm == "B" and result.get("trace"):
            result["trace"]["mode"] = "on"
            result["trace"]["steps"] = [{"kind": "perception", "dur_ms": 300, "cost_usd": 0.0001}]
            result["llm_cost_usd"], result["perception_cost_usd"] = 0.01, 0.0001
        return result

    sims["decorate"] = with_layers
    await _run(box)

    b = json.loads(box["store"].get_bytes(f"runs/{RUN}/metrics/B/0.json"))
    assert (b["arm"], b["rep"], b["profile"], b["turns"]) == ("B", 0, "jev-v1", 2)
    assert b["perception"]["p95_ms"] == 300
    a1 = json.loads(box["store"].get_bytes(f"runs/{RUN}/metrics/A1/0.json"))
    assert a1["profile"] is None and a1["perception"] is None


@pytest.mark.asyncio
async def test_a_complement_travels_with_its_case(box, sims) -> None:  # noqa: F811
    def with_complement(result: dict, arm: str) -> dict:
        if arm == "B" and result.get("trace"):
            result["complement_trace"] = {
                "session_id": SIM, "trigger": "complement", "sent_texts": ["Y el envío a Bogotá cuesta $X"],
                "llm_text": "Y el envío a Bogotá cuesta $X", "steps": [],
            }
        return result

    sims["decorate"] = with_complement
    await _run(box)

    thread = json.loads(box["store"].get_bytes(f"runs/{RUN}/threads/{SID}.json"))
    turn2 = next(t for t in thread["turns"] if t["turn"] == 2)
    assert turn2["outputs"]["B"]["complement_texts"] == ["Y el envío a Bogotá cuesta $X"]
    assert "complement_texts" not in turn2["outputs"]["A1"]
    raw = box["store"].get_bytes(f"runs/{RUN}/turns/B/0/{SID}.jsonl").decode()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert rows[0]["complement"]["sent_texts"] == ["Y el envío a Bogotá cuesta $X"]
    assert SIM not in raw


@pytest.mark.asyncio
async def test_a_failed_case_is_counted_and_the_run_goes_on(box, sims, monkeypatch) -> None:  # noqa: F811
    from src.plugins.chats.agent.sales_lab.run import activities as run_acts

    async def flaky(case, *, bench_dir, sandbox_dir, timeout_s, arm="A1"):
        sims["calls"].append((case["case_id"], sandbox_dir.parts[-3:]))
        if sandbox_dir.parts[-3] == "A1" and case["turn"] == 2:
            return _fake_result(case, error="el turno no terminó en 600 s")
        return _fake_result(case)

    monkeypatch.setattr(run_acts, "run_case_in_subprocess", flaky)

    result = await _run(box)

    assert result["phase"] == "done"
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert (progress["turns_done"], progress["turns_total"]) == (3, 4)  # A1 y B × 2 casos; falla A1 turno 2
    assert any("1 caso sin terminar" in n for n in progress["notes"])
