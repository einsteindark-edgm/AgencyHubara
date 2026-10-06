"""DecisionsProbeWorkflow — la sonda diaria de Jev (motor de decisiones, diseño v2).

Lo dispara el Temporal Schedule `decisions-probe-schedule` que crea el worker
`sales_eval` al bootear (07:00 Bogotá), o el operador a mano desde la
Temporal UI (mismo workflow, misma cola). Una sola activity:
`run_decisions_probe` hace las 20 ráfagas sintéticas con respuesta conocida,
compara con la sonda anterior y guarda el reporte; el control «Bot nuevo» lo
lee para dejar (o no) subir a canary o encendido.

R-DET: cero I/O, now() o random en `@workflow.run`: todo vive en la activity.
R-JSON: sin input; out `DecisionsProbeSummary` (frozen, escalares).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales_eval.activities.decisions_probe import run_decisions_probe_activity
    from src.plugins.chats.agent.sales_eval.evals.contracts import DecisionsProbeSummary


@workflow.defn(name="DecisionsProbeWorkflow")
class DecisionsProbeWorkflow:
    @workflow.run
    async def run(self) -> DecisionsProbeSummary:
        # 20 llamadas seguidas a Jev de 3 s como máximo cada una: 5 min sobran.
        # Un reintento solo si la activity se cae (un Jev degradado es un
        # resultado, no un error); la sonda es barata.
        summary: DecisionsProbeSummary = await workflow.execute_activity(
            run_decisions_probe_activity,
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        workflow.logger.info(
            "DecisionsProbeWorkflow: %s · %d casos · aciertos %s · Jev %s",
            summary.status, summary.cases, summary.pass_rate, summary.models or "-",
        )
        return summary
