"""Claude Code es el juez del scorecard (decisión del operador, 2026-09-28).

Gemini queda apagado. Los 13 checks de juez esperan la calificación de Claude
Code: su prompt EXACTO (mismos criterios de hoy) queda en una cola, Claude
Code responde con el mismo JSON que devolvía el juez, y el siguiente pase del
scorecard toma esa respuesta por la huella del prompt. Los checks de código no
cambian.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_eval.activities import eval_activities as ea
from src.plugins.chats.agent.sales_eval.evals import composition
from src.plugins.chats.agent.sales_eval.scorecard import claude_judge as cj
from src.plugins.chats.agent.sales_eval.scorecard import judge_checks as jc
from src.plugins.chats.agent.sales_eval.scorecard import store
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix
from tests.evals.scorecard.test_judge_checks import FakeJudge
from tests.evals.scorecard.test_judge_focus import _candidates, _turns
from tests.evals.scorecard.test_scorecard_activity import SESSION, _patch, _seed

UNIT = f"{SESSION}/ep_007"


def _ans(verdict: str, turn=None, evidence: str = "", critique: str = "") -> str:
    return json.dumps({"veredicto": verdict, "turno": turn, "evidencia": evidence, "critica": critique})


def _pending_checks(results) -> set[str]:
    return {r.check_id for r in results if r.critique == cj.PENDING_CRITIQUE}


async def test_without_my_answer_the_judge_checks_wait_and_their_exact_prompt_is_queued(tmp_path: Path) -> None:
    queue = cj.JudgeQueue(tmp_path)

    results = await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, cj.ClaudeCodeJudge(queue, unit=UNIT))

    waiting = _pending_checks(results)
    assert "CON-04" in waiting
    assert all(r.verdict == "desconocido" for r in results if r.check_id in waiting)
    assert not any(jc.is_judge_error(r) for r in results)
    items = queue.pending()
    assert {i["check_id"] for i in items} == waiting
    con04 = next(i for i in items if i["check_id"] == "CON-04")
    assert con04["prompt"] == jc.build_prompt("CON-04", pr281_before_fix(), CATALOG_CTX)
    assert con04["unit"] == UNIT


async def test_my_answer_becomes_the_verdict_of_the_check(tmp_path: Path) -> None:
    await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=UNIT))
    con04 = next(i for i in cj.JudgeQueue(tmp_path).pending() if i["check_id"] == "CON-04")

    errors = cj.JudgeQueue(tmp_path).add_answers(
        [{"id": con04["id"], "raw": _ans("falla", 9, "Voy apenas en camino", "no pidió el sí")}]
    )
    results = {
        r.check_id: r
        for r in await jc.run_judge_checks(
            pr281_before_fix(), CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=UNIT)
        )
    }

    assert errors == []
    assert (results["CON-04"].verdict, results["CON-04"].turn, results["CON-04"].critique) == (
        "falla", 9, "no pidió el sí",
    )
    assert "CON-04" not in {i["check_id"] for i in cj.JudgeQueue(tmp_path).pending()}


async def test_the_same_prompt_is_queued_once(tmp_path: Path) -> None:
    for _ in range(2):
        await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=UNIT))

    ids = [i["id"] for i in cj.JudgeQueue(tmp_path).pending()]
    assert ids and len(ids) == len(set(ids))


async def test_an_answer_that_is_not_a_verdict_is_rejected_and_the_check_keeps_waiting(tmp_path: Path) -> None:
    await jc.run_judge_checks(pr281_before_fix(), CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=UNIT))
    item = cj.JudgeQueue(tmp_path).pending()[0]

    errors = cj.JudgeQueue(tmp_path).add_answers(
        [{"id": item["id"], "raw": "creo que pasa"}, {"id": "no-existe", "raw": _ans("pasa")}]
    )

    assert len(errors) == 2
    assert cj.JudgeQueue(tmp_path).answer(item["id"]) is None
    assert item["id"] in {i["id"] for i in cj.JudgeQueue(tmp_path).pending()}


async def test_in_turn_mode_my_answer_gives_one_verdict_per_candidate(tmp_path: Path) -> None:
    real, cands = _candidates()
    unit = "lab/B/0/" + UNIT

    first = await jc.run_judge_checks_focus(
        real, cands, CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=unit), only=["EST-07"]
    )
    item = cj.JudgeQueue(tmp_path).pending()[0]
    partial = cj.JudgeQueue(tmp_path).add_answers([{"id": item["id"], "raw": _turns((3, "pasa"))}])
    full = cj.JudgeQueue(tmp_path).add_answers([{"id": item["id"], "raw": _turns((3, "pasa"), (6, "falla"))}])
    second = await jc.run_judge_checks_focus(
        real, cands, CATALOG_CTX, cj.ClaudeCodeJudge(cj.JudgeQueue(tmp_path), unit=unit), only=["EST-07"]
    )

    assert {r.critique for rs in first.values() for r in rs} == {cj.PENDING_CRITIQUE}
    assert (item["check_id"], item["turns"], item["unit"]) == ("EST-07", [3, 6], unit)
    assert len(partial) == 1, "una respuesta que no califica todas las candidatas no entra"
    assert full == []
    assert [(k, r.verdict) for k, rs in sorted(second.items()) for r in rs] == [(3, "pasa"), (6, "falla")]


async def test_production_scorecard_never_calls_gemini_and_queues_the_episode(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    _patch(monkeypatch, tmp_path, [])
    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "true")
    monkeypatch.delenv("SCORECARD_JUDGE", raising=False)

    def gemini():
        raise AssertionError("Gemini está apagado: el scorecard no debe construirlo")

    monkeypatch.setattr(composition, "get_judge", gemini)

    summary = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert not summary.error
    saved = store.find_latest(store.scorecards_dir(tmp_path), SESSION, "ep_007")
    assert any(r["critique"] == cj.PENDING_CRITIQUE for r in saved["results"])
    assert {i["unit"] for i in cj.JudgeQueue(cj.queue_dir(tmp_path)).pending()} == {UNIT}


@pytest.mark.parametrize("value", ["litellm", "gemini"])
async def test_gemini_comes_back_only_if_asked_explicitly(tmp_path: Path, monkeypatch, value: str) -> None:
    _seed(tmp_path)
    _patch(monkeypatch, tmp_path, [])
    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("SCORECARD_JUDGE", value)
    fake = FakeJudge()
    monkeypatch.setattr(composition, "get_judge", lambda: fake)

    await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert fake.prompts
    assert cj.JudgeQueue(cj.queue_dir(tmp_path)).pending() == []


async def test_on_request_claude_code_judges_even_with_the_llm_judge_switch_off(tmp_path: Path, monkeypatch) -> None:
    """El juez con IA de pago está apagado por defecto (`EVAL_LLM_JUDGE_ENABLED`,
    #383): los caminos automáticos no llaman a ningún juez. Cuando el operador
    le pide a Claude Code calificar, el recálculo a pedido (`judge_kind="claude"`)
    deja los prompts en la cola de Claude Code, sin costo de API y sin Gemini."""
    _seed(tmp_path)
    _patch(monkeypatch, tmp_path, [])
    monkeypatch.delenv("EVAL_LLM_JUDGE_ENABLED", raising=False)

    def gemini():
        raise AssertionError("Gemini está apagado: el recálculo a pedido no debe construirlo")

    monkeypatch.setattr(composition, "get_judge", gemini)

    automatic = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)
    assert cj.JudgeQueue(cj.queue_dir(tmp_path)).pending() == [], "sin el interruptor, lo automático no encola"
    assert automatic.judge is False

    await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True, "claude")

    assert {i["unit"] for i in cj.JudgeQueue(cj.queue_dir(tmp_path)).pending()} == {UNIT}

