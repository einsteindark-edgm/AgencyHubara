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
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.platform.temporal.retry_policies import _CONV_OPTIONS, _TOOL_OPTIONS
    from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
        evaluate_sales_conversation_activity,
        score_episode_scorecard_activity,
        select_conversations_to_eval_activity,
        select_scorecard_units_activity,
    )
    from src.plugins.chats.agent.sales_eval.evals.contracts import (
        ConversationEvalResult,
        EvalRunSummary,
        EvalWindowInput,
        ScorecardSummary,
    )
    from src.plugins.chats.agent.sales_eval.evals.reconstruct import parse_eval_unit_id


@workflow.defn(name="SalesEvalWorkflow")
class SalesEvalWorkflow:
    async def _score_scorecards(self, window: EvalWindowInput) -> int:
        """HU-SC-8: scorecard por etapa de los episodios con actividad en la
        ventana que el cierre no cubre (abiertos: INTERESADO, ruta humano) o
        que quedaron sin scorecard. Uno a la vez: el juez hace 13 llamadas
        por episodio y en paralelo satura el límite por minuto del proveedor.
        Nunca bloquea la eval legada."""
        try:
            units: list[str] = await workflow.execute_activity(
                select_scorecard_units_activity,
                window,
                **_CONV_OPTIONS,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001
            workflow.logger.warning(f"scorecard diario: selección falló (non-blocking): {exc!r}")
            return 0
        scored = 0
        for unit in units:
            session_id, episode_id = parse_eval_unit_id(unit)
            try:
                summary: ScorecardSummary = await workflow.execute_activity(
                    score_episode_scorecard_activity,
                    args=[session_id, episode_id, True],
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
            except Exception as exc:  # noqa: BLE001
                workflow.logger.warning(f"scorecard diario {unit} falló (non-blocking): {exc!r}")
                continue
            scored += 1 if summary.stored else 0
        return scored

    @workflow.run
    async def run(self, window: EvalWindowInput) -> EvalRunSummary:
        # patched(): runs en vuelo del schedule replayean sin la rama (R-DET).
        scorecards = (
            await self._score_scorecards(window) if workflow.patched("daily-scorecard-v1") else 0
        )
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
            scorecards=scorecards,
        )
        workflow.logger.info(
            "SalesEvalWorkflow: %d evaluadas, %d pasaron, %d candidatas a golden",
            evaluated, passed, candidates,
        )
        return summary
