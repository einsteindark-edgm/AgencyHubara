"""EvaluateEpisodeWorkflow — evalúa UN episodio puntual al cerrar (event-driven).

Disparado por el dispatcher cuando el sales worker emite `EpisodeClosedEvent`
(transition `sales_episode_close_triggers_eval` en el manifest de chats). A
diferencia de `SalesEvalWorkflow` (muestreo temporal por cron, ventana de 24h),
este es **efímero y dirigido**: evalúa el episodio que acaba de cerrar y termina.

Por qué importa: el muestreo temporal es una foto de lo reciente — un episodio
cerrado hace días nunca se evalúa. Evaluar al CERRAR da **cobertura completa**:
cada episodio queda calificado una vez, en el histórico permanente, y los malos
generan candidato a golden automáticamente. Es además más barato (1 eval por
episodio al cerrar, en vez de re-muestrear la ventana cada día).

R-DET: cero I/O en `@workflow.run`; la evaluación (DeepEval, vault, juez LLM)
vive en la activity. `make_eval_unit_id` es string puro (determinista). R-JSON:
in (`EvaluateEpisodeInput` frozen) / out (`ConversationEvalResult` frozen).
R-DIP: importa `platform/` + el propio plugin; nada de siblings ni
`temporalio.client`. Idempotencia: el workflow-id del manifest lleva
`{session_id}-{episode_id}`, así que una re-entrega del evento no arranca dos.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.platform.temporal.retry_policies import _TOOL_OPTIONS
    from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
        evaluate_sales_conversation_activity,
        score_episode_scorecard_activity,
    )
    from src.plugins.chats.agent.sales_eval.evals.contracts import (
        ConversationEvalResult,
        EvalWindowInput,
        EvaluateEpisodeInput,
        ScorecardSummary,
    )
    from src.plugins.chats.agent.sales_eval.evals.reconstruct import make_eval_unit_id


# El evento de cierre sale ANTES de que el turno de cierre termine (send, flush
# y traza). Se espera a que la traza de ese turno esté escrita.
_TRACE_GRACE = timedelta(seconds=90)


@workflow.defn(name="EvaluateEpisodeWorkflow")
class EvaluateEpisodeWorkflow:
    @workflow.run
    async def run(self, inp: EvaluateEpisodeInput) -> ConversationEvalResult:
        unit_id = make_eval_unit_id(inp.session_id, inp.episode_id)
        # HU-SC-1: scorecard por etapa (checks binarios + juez aislado por
        # check, veredicto con auto-fail). Convive con la eval legada de abajo
        # durante la transición (plan §3.7); si falla, la legada corre igual.
        # patched(): runs en vuelo replayean sin la rama (R-DET).
        if inp.episode_id and workflow.patched("scorecard-v1"):
            await workflow.sleep(_TRACE_GRACE)
            try:
                summary: ScorecardSummary = await workflow.execute_activity(
                    score_episode_scorecard_activity,
                    args=[inp.session_id, inp.episode_id, True],
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                workflow.logger.info(
                    f"scorecard {unit_id}: {summary.verdict} "
                    f"(críticos={summary.critical}, mayores={summary.major})"
                )
            except Exception as exc:  # noqa: BLE001
                workflow.logger.warning(f"scorecard {unit_id} falló (non-blocking): {exc!r}")
        # Ventana irrelevante (el unit ya viene dado); solo importan min_turns +
        # candidate_threshold + draft_goldens + redact_pii. draft_goldens=True:
        # si el episodio puntúa bajo, el juez redacta el golden y queda como
        # candidato a curar — el episodio malo se vuelve test de regresión.
        window = EvalWindowInput(
            min_turns=4,
            candidate_threshold=0.7,
            draft_goldens=True,
            redact_pii=True,
        )
        result: ConversationEvalResult = await workflow.execute_activity(
            evaluate_sales_conversation_activity,
            args=[unit_id, window],
            **_TOOL_OPTIONS,  # type: ignore[arg-type]
        )
        workflow.logger.info(
            "EvaluateEpisodeWorkflow %s (cierre %s): avg=%.3f candidate=%s",
            unit_id,
            inp.closing_tag or "?",
            result.avg_score,
            result.is_candidate,
        )
        return result
