"""Evaluación de una corrida del laboratorio (plan §5.2–5.4; PR 13).

Cada brazo se califica con el MISMO scorecard de producción en modo turno:
por episodio del banco, cada turno simulado se juzga con el prefijo REAL
como contexto (`service.score_turns`). A0 (lo que pasó) se re-mide igual,
con sus propios turnos como candidatos, para que las columnas se comparen.
El complemento del bot nuevo es parte de la respuesta de su turno.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_lab.run.evaluate import candidate_turn, score_arm
from tests.plugins.chats.lab.test_lab_cases import SID, WS, _bench


def _cases(tmp_path: Path) -> tuple[Path, list[dict]]:
    bench = _bench(tmp_path)
    return bench, [c.to_dict() for c in build_cases(bench, sales_workspace=WS).cases]


def _row(case: dict, text: str, **extra) -> dict:
    return {
        "session_id": SID, "episode_id": case["episode_id"], "turn": case["turn"], "turn_key": case["turn_key"],
        "trigger": "customer", "inbound_text": case["real"].get("inbound_text", ""), "sent_texts": [text],
        "llm_text": text, "tools": [], "guards": [], **extra,
    }


def test_a_complement_is_part_of_the_reply_of_its_turn() -> None:
    row = {"turn": 2, "trigger": "customer", "sent_texts": ["Te dejo el catálogo"], "tools": [{"name": "send_catalog", "ok": True}],
           "complement": {"sent_texts": ["Y el envío a Bogotá cuesta $12.900"], "tools": [{"name": "send_shipping_rates", "ok": True}]}}

    turn = candidate_turn(row)

    assert turn.turn == 2
    assert turn.sent_texts == ("Te dejo el catálogo", "Y el envío a Bogotá cuesta $12.900")
    assert [t.name for t in turn.tools] == ["send_catalog", "send_shipping_rates"]


@pytest.mark.asyncio
async def test_a_simulated_arm_gets_one_turn_mode_record_per_episode(tmp_path: Path) -> None:
    bench, cases = _cases(tmp_path)
    rows = {SID: [_row(c, f"simulado {c['turn']}") for c in cases]}

    records = await score_arm(bench, cases, arm="A1", rep=0, rows=rows, ctx=CheckContext(), judge=None)

    [rec] = records
    assert (rec["session_id"], rec["episode_id"], rec["arm"], rec["rep"], rec["mode"]) == (SID, "ep_001", "A1", 0, "turn")
    assert [t["turn"] for t in rec["by_turn"]] == [1, 2]
    assert rec["verdict"] in {"PASA", "ALERTA", "FALLA", "SIN_DATOS"}
    assert rec["judge"] is False and rec["episode_date"]
    assert "stage_final" in rec


@pytest.mark.asyncio
async def test_a_turn_without_a_simulated_result_is_left_out_not_invented(tmp_path: Path) -> None:
    bench, cases = _cases(tmp_path)
    rows = {SID: [_row(cases[0], "solo el primero")]}

    [rec] = await score_arm(bench, cases, arm="B", rep=1, rows=rows, ctx=CheckContext(), judge=None)

    assert [t["turn"] for t in rec["by_turn"]] == [1]
    assert rec["missing_turns"] == [2]


@pytest.mark.asyncio
async def test_the_real_control_is_re_measured_in_turn_mode(tmp_path: Path) -> None:
    bench, cases = _cases(tmp_path)

    [rec] = await score_arm(bench, cases, arm="A0", rep=0, rows=None, ctx=CheckContext(), judge=None)

    assert rec["arm"] == "A0" and [t["turn"] for t in rec["by_turn"]] == [1, 2]


@pytest.mark.asyncio
async def test_the_judge_is_asked_once_per_check_per_episode(tmp_path: Path) -> None:
    bench, cases = _cases(tmp_path)
    rows = {SID: [_row(c, f"simulado {c['turn']}") for c in cases]}

    class Judge:
        prompts: list[str] = []

        async def a_generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return '{"turnos": []}'

    judge = Judge()
    [rec] = await score_arm(bench, cases, arm="A1", rep=0, rows=rows, ctx=CheckContext(), judge=judge)

    assert judge.prompts and all("CANDIDATA" in p for p in judge.prompts)
    assert rec["judge"] is True


def test_the_episode_at_the_turn_carries_the_order_of_that_moment() -> None:
    """Revisión #358: `episodes_at` del caso trae la orden FINAL del episodio
    abierto; la del momento del turno es la del estado anterior
    (`state_before`). Sin eso, un turno anterior a la orden la vería."""
    from src.plugins.chats.agent.sales_lab.run.evaluate import episode_at

    case = {"episode_id": "ep_001", "episodes_at": [{"episode_id": "ep_001", "order_id": "ord_9", "started_at_ms": 5}],
            "state_before": {"order_id": None}}
    assert episode_at(case) == {"episode_id": "ep_001", "order_id": None, "started_at_ms": 5}

    later = {**case, "state_before": {"order_id": "ord_9"}}
    assert episode_at(later)["order_id"] == "ord_9"
    assert episode_at({"episode_id": "ep_002", "episodes_at": []}) is None
