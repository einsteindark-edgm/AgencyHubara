"""La sonda diaria de Jev en el worker `sales_eval`: `DecisionsProbeWorkflow`
corre UNA activity (`run_decisions_probe`) que hace las 20 ráfagas sintéticas,
compara con la sonda anterior, guarda el reporte en el vault y avisa en el log
si quedó `degraded` o `down`.

Jev va falso (el puerto del SDK, parchado) y el vault es temporal.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.decisions import probe
from src.plugins.chats.agent.sales_eval.activities import decisions_probe as activities
from src.plugins.chats.agent.sales_eval.evals.contracts import DecisionsProbeSummary
from src.plugins.chats.agent.sales_eval.workflows.decisions_probe import DecisionsProbeWorkflow
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

SERVED = "typesafe/jev-1.13-20260917"
QUEUE = "queue-decisions-probe-test"


class _Jev:
    """Jev falso que acierta todas las respuestas conocidas de la sonda."""

    def __init__(self, model: str = SERVED) -> None:
        self.model = model
        self.calls = 0
        self.expected = {probe.build_request(c)[0]: c.expect for c in probe.PROBE_CASES}

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.calls += 1
        want = self.expected.get(state, {})
        answers = []
        for q in questions:
            expected = want.get(q.id)
            if q.kind == "noul":
                answers.append(TypedAnswer(id=q.id, kind="noul", p=0.9 if expected is True else 0.1))
            else:
                pick = expected if isinstance(expected, str) else q.options[0]
                answers.append(TypedAnswer(id=q.id, kind="choice", choice=pick, probs=((pick, 1.0),), confidence=0.9))
        return PerceptionResult(ok=True, answers=tuple(answers), provider="fake", model=self.model, latency_ms=700)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(activities, "WORKSPACE_VAULT_DIR", tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def jev(monkeypatch) -> _Jev:
    from src.sdk import connectorkit

    port = _Jev()

    def _get(_oracle: str) -> _Jev:
        return port

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    return port


def _latest(vault: Path) -> dict:
    return json.loads((vault / "_decisions" / "probe" / "latest.json").read_text(encoding="utf-8"))


async def test_the_activity_runs_the_probe_and_keeps_the_report(vault: Path, jev: _Jev) -> None:
    out = await ActivityEnvironment().run(activities.run_decisions_probe_activity)

    assert out.status == "ok" and out.cases == 20 and jev.calls == 20
    assert (out.ok_rate, out.pass_rate, out.p95_ms) == (1.0, 1.0, 700)
    assert out.models == SERVED and (out.shape_errors, out.failures) == (0, 0)
    latest = _latest(vault)
    assert latest["status"] == "ok" and latest["at_ms"] == out.at_ms and len(latest.get("failures")) == 0
    assert len(list((vault / "_decisions" / "probe").glob("20??-??-??.json"))) == 1


async def test_a_new_snapshot_since_the_last_probe_is_degraded_and_logged(
    vault: Path, jev: _Jev, caplog: pytest.LogCaptureFixture
) -> None:
    probe.write_report(vault, {"at_ms": 1, "status": "ok", "models": ["typesafe/jev-1.13-20260901"], "cases": 20,
                               "ok_rate": 1.0, "pass_rate": 1.0, "p95_ms": 700, "shape_errors": [], "failures": []})

    with caplog.at_level(logging.WARNING):
        out = await ActivityEnvironment().run(activities.run_decisions_probe_activity)

    assert out.status == "degraded" and _latest(vault)["status"] == "degraded"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("degraded" in m and "typesafe/jev-1.13-20260901" in m for m in warnings), warnings


async def test_without_a_key_it_calls_nobody_and_says_so(vault: Path, jev: _Jev, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER_set_out_of_band")

    out = await ActivityEnvironment().run(activities.run_decisions_probe_activity)

    assert out.status == "sin_llave" and jev.calls == 0
    assert _latest(vault)["status"] == "sin_llave"


async def test_the_workflow_runs_the_probe_once_and_returns_its_summary(vault: Path, jev: _Jev) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[DecisionsProbeWorkflow],
            activities=[activities.run_decisions_probe_activity],
        ):
            out = await env.client.execute_workflow(DecisionsProbeWorkflow.run, id="decisions-probe", task_queue=QUEUE)

    assert isinstance(out, DecisionsProbeSummary)
    assert out.status == "ok" and out.cases == 20 and jev.calls == 20
    assert _latest(vault)["status"] == "ok"
