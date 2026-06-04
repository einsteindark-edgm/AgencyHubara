"""SalesEvalWorkflow — disparado por un Temporal Schedule (ej. 3×/día).

Orquesta la evaluación online de calidad del Asesor de Ventas: selecciona las
conversaciones de la ventana, las evalúa en fan-out por chunks (acota la carga
sobre el LLM-juez) y agrega un resumen escalar. El detalle por-métrica de cada
conversación lo emite la activity a SigNoz; el workflow solo cuenta.

R-DET: cero I/O, cero env, cero now()/random en `@workflow.run`. Todo el
no-determinismo (DeepEval, lectura del vault, juez LLM) vive en las activities.
`deepeval` NUNCA se importa acá. El fan-out por chunks + slicing de listas es
determinista; `asyncio.gather` de activity handles es el patrón estándar de
Temporal (event loop determinista).
"""
from __future__ import annotations

import asyncio

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.platform.temporal.retry_policies import _CONV_OPTIONS, _TOOL_OPTIONS
    from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
        evaluate_sales_conversation_activity,
        select_conversations_to_eval_activity,
    )
    from src.plugins.chats.agent.sales_eval.evals.contracts import (
        ConversationEvalResult,
        EvalRunSummary,
        EvalWindowInput,
    )


@workflow.defn(name="SalesEvalWorkflow")
class SalesEvalWorkflow:
    @workflow.run
    async def run(self, window: EvalWindowInput) -> EvalRunSummary:
        sessions: list[str] = await workflow.execute_activity(
            select_conversations_to_eval_activity,
            window,
            **_CONV_OPTIONS,  # type: ignore[arg-type]
        )

        results: list[ConversationEvalResult] = []
        chunk = max(1, window.chunk_size)
        for i in range(0, len(sessions), chunk):
            batch = sessions[i : i + chunk]
            batch_results = await asyncio.gather(
                *[
                    workflow.execute_activity(
                        evaluate_sales_conversation_activity,
                        args=[sid, window],
                        **_TOOL_OPTIONS,  # type: ignore[arg-type]
                    )
                    for sid in batch
                ]
            )
            results.extend(batch_results)

        evaluated = sum(1 for r in results if not r.skipped and not r.error)
        passed = sum(
            1 for r in results if r.overall_pass and not r.skipped and not r.error
        )
        candidates = sum(1 for r in results if r.is_candidate)
        skipped = sum(1 for r in results if r.skipped)
        errors = sum(1 for r in results if r.error and not r.skipped)

        summary = EvalRunSummary(
            window_hours=window.lookback_hours,
            selected=len(sessions),
            evaluated=evaluated,
            passed=passed,
            failed=evaluated - passed,
            candidates=candidates,
            skipped=skipped,
            errors=errors,
        )
        workflow.logger.info(
            "SalesEvalWorkflow: %d evaluadas, %d pasaron, %d candidatas a golden",
            evaluated, passed, candidates,
        )
        return summary
