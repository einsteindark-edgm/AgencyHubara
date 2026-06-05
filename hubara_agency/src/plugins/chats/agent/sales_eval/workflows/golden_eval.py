"""GoldenEvalWorkflow — corre el GOLDEN set (el MISMO eval del GitHub Action) y
emite los scores a SigNoz, disparado por un Temporal Schedule (default 1×/día).

A diferencia de `SalesEvalWorkflow` (muestrea conversaciones REALES de la ventana),
esto corre el set de escenarios CONTROLADO — el agente real es manejado por cada
escenario y los scores caen en el MISMO dashboard "Calidad del LLM" con
`eval.suite=golden`. Así, el benchmark que ves en CI (GitHub Action) y el que ves
en SigNoz son el MISMO eval.

R-DET: cero I/O / now() / random en `@workflow.run`. Todo el no-determinismo (el
subprocess del runner, el juez LLM, la emisión a SigNoz) vive en la activity. El
workflow solo dispara la activity (de larga duración) y devuelve el agregado.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

    from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
        run_golden_suite_activity,
    )
    from src.plugins.chats.agent.sales_eval.evals.contracts import (
        GoldenEvalInput,
        GoldenSuiteResult,
    )


@workflow.defn(name="GoldenEvalWorkflow")
class GoldenEvalWorkflow:
    @workflow.run
    async def run(self, inp: GoldenEvalInput) -> GoldenSuiteResult:
        # Una sola activity: corre los 61 escenarios en UN subprocess (amortiza el
        # costo de import y mantiene un solo stream de llamadas al LLM -> evita
        # rate-limits que tendría un fan-out paralelo). Timeout largo + heartbeat.
        # El subprocess ya es internamente resiliente a transitorios del LLM, así
        # que NO reintentamos la suite entera (sería ~90 min desperdiciados); si
        # falla, el próximo cron la vuelve a correr.
        result: GoldenSuiteResult = await workflow.execute_activity(
            run_golden_suite_activity,
            inp,
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        workflow.logger.info(
            "GoldenEvalWorkflow: %d escenarios · %d behaviors-ok · %d errores (%ds)",
            result.scenarios, result.behaviors_ok, result.errored, result.duration_s,
        )
        return result
