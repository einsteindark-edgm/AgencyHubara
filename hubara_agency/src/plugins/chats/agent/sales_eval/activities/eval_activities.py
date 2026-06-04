"""Activities del harness de evaluación (no-deterministas: corren DeepEval).

R-DET: la evaluación LLM-as-judge es I/O + no-determinismo → vive ACÁ, NUNCA en
el workflow. `deepeval` se usa solo en runtime (worker con extra `evals`); los
imports del paquete `evals` son lazy/guarded, así que este módulo es importable
sin el extra (gate de arquitectura no rompe).

R-HEARTBEAT: `evaluate_sales_conversation_activity` corre ~9 métricas (varias con
llamadas al juez, >10s worst-case) → `@with_heartbeat`.
R-JSON: in (str, EvalWindowInput) / out (ConversationEvalResult) — frozen scalars.
R-DIP: importa `platform/` + el propio plugin sales; NO importa plugins siblings
ni `temporalio.client`.
"""
from __future__ import annotations

import os

from temporalio import activity

from src.platform.observability.eval_metrics import (
    emit_conversation_verdict,
    emit_eval_score,
)
from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.chats.agent.sales_eval.evals import (
    composition,
    curation,
    reconstruct,
    script_rubric,
)
from src.plugins.chats.agent.sales_eval.evals import metrics as M
from src.plugins.chats.agent.sales_eval.evals.contracts import (
    ConversationEvalResult,
    EvalWindowInput,
)
from src.plugins.chats.agent.sales_eval.evals.select import select_sessions

_SCENARIO = "Conversación real de ventas por WhatsApp (Hubara)."


def _env() -> str:
    return os.getenv("ENVIRONMENT", "dev")


@activity.defn(name="select_conversations_to_eval")
async def select_conversations_to_eval_activity(window: EvalWindowInput) -> list[str]:
    """Enumera + prioriza las conversaciones a evaluar en la ventana (lee vault)."""
    sessions = select_sessions(window, vault_dir=composition.get_vault_dir())
    activity.logger.info(
        "eval.select: %d conversaciones seleccionadas (ventana %dh, tope %d)",
        len(sessions), window.lookback_hours, window.max_conversations,
    )
    return sessions


@activity.defn(name="evaluate_sales_conversation")
@with_heartbeat(every=10)
async def evaluate_sales_conversation_activity(
    session_id: str, window: EvalWindowInput
) -> ConversationEvalResult:
    """Evalúa UNA conversación: reconstruye → puntúa → emite a SigNoz → (auto-curación).

    El detalle por-métrica se emite a SigNoz dentro de esta activity (span attrs +
    métrica). El return es escalar (lo agrega el workflow). Si una conversación es
    candidata a golden y `draft_goldens`, el juez redacta el `expected_outcome`.
    """
    vault_dir = composition.get_vault_dir()
    wa_number = reconstruct.whatsapp_number_from_session(session_id)

    events = reconstruct.read_session_events(vault_dir, session_id)
    turns = reconstruct.to_evaluable_turns(events, redact=window.redact_pii)
    if len(turns) < window.min_turns:
        return ConversationEvalResult(
            session_id=session_id, whatsapp_number=wa_number, num_turns=len(turns),
            skipped=True, error="too_few_turns",
        )

    test_case = reconstruct.build_conversational_test_case(
        turns, scenario=_SCENARIO, context=[script_rubric.SCRIPT_CONTEXT], name=session_id,
    )

    judge = composition.get_judge()
    metrics = M.all_sales_metrics(judge)

    # scores: [(metric_key, score, success, reason)]
    scores: list[tuple[str, float, bool, str]] = []
    for metric in metrics:
        key = M.metric_key(metric)
        try:
            await metric.a_measure(test_case)
            score = float(getattr(metric, "score", 0.0) or 0.0)
            success = bool(metric.is_successful())
            reason = str(getattr(metric, "reason", "") or "")
        except Exception as exc:  # noqa: BLE001 — una métrica que falla no tumba la corrida
            activity.logger.warning("eval métrica %s falló: %s", key, exc)
            continue
        scores.append((key, score, success, reason))
        emit_eval_score(
            metric_name=key, score=score, threshold=float(getattr(metric, "threshold", 0.5)),
            reason=reason, session_id=session_id, whatsapp_number=wa_number,
            agent="sales-agent", environment=_env(),
        )
        activity.heartbeat(key)

    if not scores:
        return ConversationEvalResult(
            session_id=session_id, whatsapp_number=wa_number, num_turns=len(turns),
            error="no_metrics_evaluated",
        )

    n = len(scores)
    n_pass = sum(1 for (_, _, ok, _) in scores if ok)
    avg = sum(s for (_, s, _, _) in scores) / n
    overall_pass = n_pass == n
    # Candidata a golden: el promedio cae bajo el umbral (la conversación está por
    # debajo de la barra global). Tunable vía `window.candidate_threshold`.
    is_candidate = avg < window.candidate_threshold

    emit_conversation_verdict(
        session_id=session_id, avg_score=avg, overall_pass=overall_pass,
        is_candidate=is_candidate, num_metrics=n, environment=_env(),
    )

    candidate_path = ""
    if is_candidate and window.draft_goldens:
        try:
            failed = [(k, s, r) for (k, s, ok, r) in scores if not ok]
            expected = await curation.propose_expected_outcome(judge, turns, failed)
            golden = curation.build_candidate_golden(
                session_id=session_id, turns=turns, scenario=_SCENARIO,
                expected_outcome=expected, scores=scores,
            )
            path = curation.write_candidate(
                composition.get_candidates_dir(), session_id, golden
            )
            candidate_path = str(path)
            activity.logger.info("eval.candidate escrito: %s", candidate_path)
        except Exception as exc:  # noqa: BLE001 — la curación no rompe la evaluación
            activity.logger.warning("eval golden draft falló: %s", exc)

    return ConversationEvalResult(
        session_id=session_id, whatsapp_number=wa_number, num_turns=len(turns),
        metrics_evaluated=n, metrics_passed=n_pass, avg_score=round(avg, 4),
        overall_pass=overall_pass, is_candidate=is_candidate, candidate_path=candidate_path,
    )
