"""Emisión de scores de evaluación LLM-as-judge a SigNoz (OTel).

Genérico y agnóstico de DeepEval: solo depende de `opentelemetry`. Lo usa el
harness de evals (`plugins/chats/.../evals`) para escribir los resultados de cada
evaluación de vuelta al backend de observabilidad, cerrando el loop
"trackear conversaciones → evaluar → ver en el tablero".

Diseño de cardinalidad (importante para no reventar ClickHouse de métricas):

  * **Métrica** `gen_ai.eval.score` (histograma, valor 0..1) lleva SOLO atributos
    de BAJA cardinalidad: `metric.name`, `verdict` (pass/fail), `gen_ai.agent`,
    `deployment.environment`. Sirve para tendencias y tableros ("score promedio
    de adherencia al guion en el tiempo", "pass rate por métrica").
  * **Span** (el de la activity de evaluación, una por conversación) lleva el
    detalle de ALTA cardinalidad: `session.id`, `episode.id`, `whatsapp.number`,
    y por cada métrica `gen_ai.eval.<metric>.score/.verdict/.reason`. Eso da
    drill-down en Traces (qué conversación falló adherencia y por qué) sin
    explotar la cardinalidad de métricas.

Degrada sin romper: con `OTEL_SDK_DISABLED=true` el provider global es no-op y
`record`/`set_attribute` no hacen nada. Si algo falla, se loguea y sigue — la
observabilidad NUNCA tumba la evaluación.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_histogram = None  # singleton lazy del instrumento de métrica


def _get_histogram():
    global _histogram
    if _histogram is None:
        from opentelemetry import metrics

        meter = metrics.get_meter("hubara.evals")
        _histogram = meter.create_histogram(
            name="gen_ai.eval.score",
            unit="1",
            description="Score de evaluación LLM-as-judge (0..1) por métrica por conversación",
        )
    return _histogram


def _verdict(score: float, threshold: float) -> str:
    return "pass" if score >= threshold else "fail"


def emit_eval_score(
    *,
    metric_name: str,
    score: float,
    threshold: float,
    reason: str = "",
    session_id: str = "",
    episode_id: str = "",
    whatsapp_number: str = "",
    agent: str = "sales-agent",
    environment: str = "dev",
    suite: str = "online",
) -> None:
    """Emite un score de evaluación: métrica (baja card) + span attrs (alta card).

    Pensado para llamarse DENTRO de la activity de evaluación, donde el span
    activo es el de "evaluar conversación X" — así los atributos de detalle caen
    en ESE span y quedan navegables en SigNoz Traces junto al trace de prod.

    `suite` (baja card): 'online' (muestreo de conversaciones reales) vs 'golden'
    (set de escenarios controlado, el mismo del GitHub Action) — el dashboard los
    separa con un filtro.
    """
    verdict = _verdict(score, threshold)

    # 1) Métrica de baja cardinalidad → tableros / alertas de drift.
    try:
        _get_histogram().record(
            float(score),
            attributes={
                "metric.name": metric_name,
                "verdict": verdict,
                "gen_ai.agent": agent,
                "deployment.environment": environment,
                "eval.suite": suite,
            },
        )
    except Exception as exc:  # noqa: BLE001 — observabilidad nunca rompe
        logger.warning("emit_eval_score: fallo al grabar métrica: %s", exc)

    # 2) Detalle de alta cardinalidad → atributos del span activo (drill-down).
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None:
            span.set_attribute("eval.suite", suite)
            prefix = f"gen_ai.eval.{metric_name}"
            span.set_attribute(f"{prefix}.score", float(score))
            span.set_attribute(f"{prefix}.verdict", verdict)
            if reason:
                span.set_attribute(f"{prefix}.reason", reason[:600])
            if session_id:
                span.set_attribute("session.id", session_id)
            if episode_id:
                span.set_attribute("episode.id", episode_id)
            if whatsapp_number:
                span.set_attribute("whatsapp.number", whatsapp_number)
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_eval_score: fallo al anotar span: %s", exc)


def emit_conversation_verdict(
    *,
    session_id: str,
    avg_score: float,
    overall_pass: bool,
    is_candidate: bool,
    num_metrics: int,
    agent: str = "sales-agent",
    environment: str = "dev",
    suite: str = "online",
) -> None:
    """Emite el veredicto agregado de una conversación (score promedio + flags).

    Métrica `gen_ai.eval.conversation` (histograma del avg) con baja cardinalidad
    + flags en el span. Útil para "qué % de conversaciones pasa el umbral global".
    """
    try:
        from opentelemetry import metrics

        meter = metrics.get_meter("hubara.evals")
        meter.create_histogram(
            name="gen_ai.eval.conversation",
            unit="1",
            description="Score promedio de evaluación por conversación (0..1)",
        ).record(
            float(avg_score),
            attributes={
                "verdict": "pass" if overall_pass else "fail",
                "candidate": str(is_candidate).lower(),
                "gen_ai.agent": agent,
                "deployment.environment": environment,
                "eval.suite": suite,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_conversation_verdict: fallo métrica: %s", exc)

    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None:
            span.set_attribute("gen_ai.eval.avg_score", float(avg_score))
            span.set_attribute("gen_ai.eval.overall_pass", overall_pass)
            span.set_attribute("gen_ai.eval.is_candidate", is_candidate)
            span.set_attribute("gen_ai.eval.num_metrics", num_metrics)
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_conversation_verdict: fallo span: %s", exc)
