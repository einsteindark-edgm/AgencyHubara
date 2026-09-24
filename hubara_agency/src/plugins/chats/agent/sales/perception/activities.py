"""Activities de las capas ① (percibir la ráfaga) y ③ (verificar la respuesta).

Llaman al clasificador por el puerto del SDK (`get_perception_port(perfil)`)
y NUNCA fallan: el puerto ya devuelve `ok=False` ante timeout, error del
proveedor, llave ausente o respuesta con otra forma, y acá cualquier
excepción inesperada también se convierte en un resultado vacío. Así el
turno sigue como hoy (fail-open) y la traza guarda el motivo.
"""
from __future__ import annotations

from temporalio import activity

from src.plugins.chats.agent.sales.perception.contracts import (
    PerceiveInput,
    PerceiveOutput,
    VerifyInput,
    VerifyOutput,
)
from src.plugins.chats.agent.sales.perception.plan import (
    PlanTopic,
    TurnPlan,
    coverage_decision,
    plan_from_answers,
)
from src.plugins.chats.agent.sales.perception.questions import (
    burst_state,
    rafaga_questions,
    reply_state,
    verify_questions,
)

TIMEOUT_S = 3.0


def _answers_for_trace(result, *, picked: set[str], detect: float = 0.70) -> list[dict]:
    out = []
    for a in result.answers:
        out.append(
            {
                "q": a.id,
                "type": a.kind,
                "p": a.p,
                "choice": a.choice,
                "confidence": a.confidence,
                "picked": a.id in picked if a.kind == "noul" else None,
            }
        )
    return out


@activity.defn(name="perceive_burst")
async def perceive_burst_activity(inp: PerceiveInput) -> PerceiveOutput:
    from src.sdk.connectorkit import get_perception_port

    try:
        port = get_perception_port(inp.profile)
        state = burst_state(inp.messages, pending=inp.pending, last_bot_text=inp.last_bot_text)
        result = await port.ask(state, rafaga_questions(inp.messages), timeout_s=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — fail-open: el turno sale como hoy
        return PerceiveOutput(ok=False, profile=inp.profile, error=f"unexpected: {exc!r}"[:300])
    plan = plan_from_answers(result, n_messages=len(inp.messages))
    picked = {f"topic.{t.topic}" for t in plan.topics}
    return PerceiveOutput(
        ok=plan.ok,
        profile=inp.profile,
        model=result.model,
        topics=[{"topic": t.topic, "msg": t.msg, "p": t.p} for t in plan.topics],
        stage=plan.stage,
        answers=_answers_for_trace(result, picked=picked),
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="verify_coverage")
async def verify_coverage_activity(inp: VerifyInput) -> VerifyOutput:
    from src.sdk.connectorkit import get_perception_port

    plan = TurnPlan(
        ok=True,
        topics=tuple(PlanTopic(str(t.get("topic")), t.get("msg"), t.get("p")) for t in inp.topics if t.get("topic")),
    )
    if not plan.topics:
        return VerifyOutput(ok=True, decision="send")
    try:
        port = get_perception_port(inp.profile)
        result = await port.ask(
            reply_state(inp.messages, inp.reply_text, inp.components), verify_questions(plan), timeout_s=TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        return VerifyOutput(ok=False, decision="send", error=f"unexpected: {exc!r}"[:300])
    decision = coverage_decision(plan, result)
    covered = {f"cover.{k}" for k, p in decision.covered.items() if p >= 0.70}
    return VerifyOutput(
        ok=result.ok,
        decision=decision.decision,
        missing=list(decision.missing),
        answers=_answers_for_trace(result, picked=covered),
        model=result.model,
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
    )


PERCEPTION_ACTIVITIES = [perceive_burst_activity, verify_coverage_activity]
