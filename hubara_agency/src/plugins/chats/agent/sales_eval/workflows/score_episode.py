"""ScoreEpisodeWorkflow — recalcula el scorecard de UN episodio a demanda (HU-SC-2).

Sin espera ni eval legada: solo la activity del scorecard. Lo arranca la API
("Recalcular con juez" en el dashboard). R-DET: cero I/O en el workflow; R-JSON:
in `ScoreEpisodeInput` / out `ScorecardSummary`, ambos frozen y escalares.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
        score_episode_scorecard_activity,
    )
    from src.plugins.chats.agent.sales_eval.evals.contracts import (
        ScoreEpisodeInput,
        ScorecardSummary,
    )


@workflow.defn(name="ScoreEpisodeWorkflow")
class ScoreEpisodeWorkflow:
    @workflow.run
    async def run(self, inp: ScoreEpisodeInput) -> ScorecardSummary:
        return await workflow.execute_activity(
            score_episode_scorecard_activity,
            args=[inp.session_id, inp.episode_id, inp.with_judge],
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
