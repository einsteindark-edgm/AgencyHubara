"""Claude Code es el juez del laboratorio (decisión del operador, 2026-09-28).

La caja califica los checks de código como siempre y deja los prompts de los
checks de juez en `runs/<corrida>/judge/pending.jsonl`. Claude Code los
califica y sube `runs/<corrida>/judge/answers.jsonl`. Una corrida en modo
«solo evaluar» aplica esas respuestas sin volver a simular. El juez ya no
cuesta API: no entra al estimado ni a la reserva del tope.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import PENDING_CRITIQUE
from src.plugins.chats.agent.sales_lab.launch.costs import AGENT_USD_PER_TURN, JUDGE_USD_PER_TURN, estimate_run_usd
from src.plugins.chats.agent.sales_lab.run import activities as run_acts
from src.plugins.chats.agent.sales_lab.run.contracts import LAB_TASK_QUEUE, LabRunInput
from src.plugins.chats.agent.sales_lab.run.workflow import LabRunWorkflow
from tests.plugins.chats.lab.test_lab_run_arms import sims  # noqa: F401  (fixture)
from tests.plugins.chats.lab.test_lab_run_box import RUN, _run, box  # noqa: F401  (fixture)
from tests.plugins.chats.lab.test_lab_run_evaluation_premortem import _order, _progress, _summary


def _jsonl(box, key: str) -> list[dict]:  # noqa: F811
    raw = box["store"].get_bytes(key) or b""
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def _pending(box) -> list[dict]:  # noqa: F811
    answered = {a["id"] for a in _jsonl(box, f"runs/{RUN}/judge/answers.jsonl")}
    return [i for i in _jsonl(box, f"runs/{RUN}/judge/pending.jsonl") if i["id"] not in answered]


def _answer_all(box) -> None:  # noqa: F811
    lines = []
    for item in _pending(box):
        raw = json.dumps({"turnos": [
            {"turno": k, "veredicto": "pasa", "evidencia": "", "critica": "ok"} for k in item["turns"]
        ]})
        lines.append(json.dumps({"id": item["id"], "raw": raw, "by": "claude-code"}))
    box["store"].put_bytes(f"runs/{RUN}/judge/answers.jsonl", ("\n".join(lines) + "\n").encode())


def _critiques(box, arm: str) -> set[str]:  # noqa: F811
    out = set()
    for key in box["store"].list_keys(f"runs/{RUN}/scores/{arm}/0/"):
        for rec in _jsonl(box, key):
            for t in rec.get("by_turn") or []:
                out |= {r.get("critique") or "" for r in t.get("results") or []}
    return out


async def _evaluate_only(box) -> dict:  # noqa: F811
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=LAB_TASK_QUEUE, workflows=[LabRunWorkflow], activities=run_acts.LAB_RUN_ACTIVITIES):
            return await env.client.execute_workflow(
                LabRunWorkflow.run, LabRunInput(run_id=RUN, mode="evaluate"),
                id=f"lab-eval-{RUN}", task_queue=LAB_TASK_QUEUE,
            )


@pytest.mark.asyncio
async def test_the_box_leaves_the_judge_prompts_in_s3_and_says_how_many_wait(box, sims, monkeypatch) -> None:  # noqa: F811
    _order(box, arms=["A1"], reps=1)
    monkeypatch.delenv("LAB_JUDGE", raising=False)

    result = await _run(box)

    assert result["phase"] == "done"
    pending = _pending(box)
    assert pending and all(i["turns"] for i in pending)
    assert {i["unit"].split("/", 1)[0] for i in pending} == {"A0", "A1"}
    assert PENDING_CRITIQUE in _critiques(box, "A1")
    assert _summary(box)["judge"]["pending"] == len(pending)
    assert any("esperan a Claude Code" in n for n in _progress(box)["notes"])
    assert _progress(box)["spent_usd"] == pytest.approx(3 * 0.01, abs=1e-6), "el juez no gasta API"


@pytest.mark.asyncio
async def test_an_evaluate_only_run_applies_my_answers_without_simulating_again(box, sims, monkeypatch) -> None:  # noqa: F811
    _order(box, arms=["A1"], reps=1)
    monkeypatch.delenv("LAB_JUDGE", raising=False)
    await _run(box)
    simulated = len(sims["calls"])
    _answer_all(box)

    result = await _evaluate_only(box)

    assert result["phase"] == "done"
    assert len(sims["calls"]) == simulated, "solo evaluar no vuelve a simular"
    assert _pending(box) == []
    assert PENDING_CRITIQUE not in _critiques(box, "A1")
    assert _summary(box)["judge"]["pending"] == 0
    assert _progress(box)["phase"] == "done"


def test_the_estimate_does_not_charge_the_claude_code_judge() -> None:
    claude = estimate_run_usd(["A1"], reps=1, turns=1000)
    gemini = estimate_run_usd(["A1"], reps=1, turns=1000, judge_usd_per_turn=JUDGE_USD_PER_TURN)

    assert claude < gemini
    assert claude == pytest.approx(1000 * AGENT_USD_PER_TURN, abs=0.01), "solo el agente: A1 no tiene clasificador"


def test_the_box_worker_reads_the_mode_of_the_order() -> None:
    from src.plugins.chats.workers.sales_lab import run_input_from_env

    full, full_id = run_input_from_env({"LAB_RUN_ID": RUN})
    evaluate, eval_id = run_input_from_env({"LAB_RUN_ID": RUN, "LAB_RUN_MODE": "evaluate"})

    assert (full.mode, full_id) == ("full", f"lab-run-{RUN}")
    assert (evaluate.mode, eval_id) == ("evaluate", f"lab-eval-{RUN}")
