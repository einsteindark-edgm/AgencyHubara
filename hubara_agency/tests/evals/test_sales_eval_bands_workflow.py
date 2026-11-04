"""El barrido diario (`SalesEvalWorkflow`, 23:00 Bogotá) corre las bandas por
regla con alertas, una vez, después de calificar los episodios del día y antes
de la eval legada (Ciclo Colossus F4). Una falla de las bandas no frena nada."""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_eval.evals.contracts import (
    BandsInput,
    BandsSummary,
    ConversationEvalResult,
    EvalRunSummary,
    EvalWindowInput,
    ScorecardSummary,
)
from src.plugins.chats.agent.sales_eval.workflows.sales_eval import SalesEvalWorkflow


def _fakes(calls: list, *, bands_broken: bool = False) -> list:
    @activity.defn(name="select_scorecard_units")
    async def units(window: EvalWindowInput) -> list[str]:
        return ["wa_100000000001::ep_001"]

    @activity.defn(name="score_episode_scorecard")
    async def score(session_id: str, episode_id: str, with_judge: bool) -> ScorecardSummary:
        calls.append(("scorecard", session_id))
        return ScorecardSummary(session_id=session_id, episode_id=episode_id, verdict="PASA", stored=True)

    @activity.defn(name="compute_scorecard_bands")
    async def compute_bands(inp: BandsInput) -> BandsSummary:
        calls.append(("bands", inp.alert))
        if bands_broken:
            raise RuntimeError("vault ilegible")
        return BandsSummary(to_open=2, opened=1, commented=1)

    @activity.defn(name="select_conversations_to_eval")
    async def select(window: EvalWindowInput) -> list[str]:
        return ["wa_100000000001::ep_001"]

    @activity.defn(name="evaluate_sales_conversation")
    async def evaluate(unit_id: str, window: EvalWindowInput) -> ConversationEvalResult:
        calls.append(("legacy", unit_id))
        return ConversationEvalResult(session_id=unit_id)

    return [units, score, compute_bands, select, evaluate]


async def _run(calls: list, queue: str, **kw) -> EvalRunSummary:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=queue, workflows=[SalesEvalWorkflow], activities=_fakes(calls, **kw)):
            return await env.client.execute_workflow(
                SalesEvalWorkflow.run, EvalWindowInput(lookback_hours=24), id=f"{queue}-1", task_queue=queue,
            )


@pytest.mark.asyncio
async def test_the_daily_sweep_runs_the_bands_with_alerts_after_scoring() -> None:
    calls: list = []

    summary = await _run(calls, "sales-eval-bands")

    assert calls == [
        ("scorecard", "wa_100000000001"),
        ("bands", True),
        ("legacy", "wa_100000000001::ep_001"),
    ]
    assert summary.bands_to_open == 2


@pytest.mark.asyncio
async def test_broken_bands_never_stop_the_sweep() -> None:
    calls: list = []

    summary = await _run(calls, "sales-eval-bands-broken", bands_broken=True)

    assert ("legacy", "wa_100000000001::ep_001") in calls
    assert summary.bands_to_open == 0
    assert summary.scorecards == 1
