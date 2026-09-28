"""Núcleo del motor de decisiones: perfil → cuestionario → Jev → política.

Sin Temporal y sin I/O propio (salvo la llamada a Jev por el puerto del SDK):
lo que necesita del vault se lo pasa quien lo llama (las activities, y en F6
las guardas de las tools). Así el mismo núcleo sirve a activities y tools, y
el laboratorio lo corre igual.

NUNCA lanza: un perfil desconocido, Jev caído, tarde o con otra forma dan un
resultado vacío (`ok=False`) con el motivo, y el consumidor sigue como hoy.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from src.plugins.chats.agent.sales.decisions.contracts import (
    CONTRACT_VERSION,
    PerceiveInput,
    TurnDecisions,
    VerifyInput,
    VerifyOutput,
)
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan
from src.plugins.chats.agent.sales.decisions.policies import get_policy
from src.plugins.chats.agent.sales.decisions.profiles import EngineProfile, get_engine_profile
from src.plugins.chats.agent.sales.decisions.questionnaire import Questionnaire, load_questionnaire

logger = structlog.get_logger()

ERROR_UNKNOWN_PROFILE = "unknown_profile"


def _resolve(profile_id: str) -> tuple[EngineProfile, Questionnaire, Any] | None:
    profile = get_engine_profile(profile_id)
    if profile is None:
        return None
    try:
        return profile, load_questionnaire(profile.questions), get_policy(profile.policy)
    except KeyError as exc:  # perfil mal armado: el turno sale como hoy
        logger.warning("decisions.bad_profile", profile=profile_id, error=str(exc))
        return None


def _oracle(profile: EngineProfile) -> tuple[Any, float]:
    from src.sdk.connectorkit import get_perception_port, oracle_timeout_s

    return get_perception_port(profile.oracle), oracle_timeout_s(profile.oracle)


def _answers_for_trace(result: Any, *, picked: set[str]) -> list[dict]:
    return [
        {
            "q": a.id,
            "type": a.kind,
            "p": a.p,
            "choice": a.choice,
            "confidence": a.confidence,
            "picked": a.id in picked if a.kind == "noul" else None,
        }
        for a in result.answers
    ]


def _versions(profile: EngineProfile, model: str) -> dict[str, str]:
    return {"profile": profile.id, "questions": profile.questions, "policy": profile.policy, "model": model}


async def perceive(inp: PerceiveInput, *, redact: Sequence[str] = ()) -> TurnDecisions:
    """① Antes del turno: los asuntos de la ráfaga, la nota y las reglas de ②."""
    resolved = _resolve(inp.profile)
    if resolved is None:
        return TurnDecisions(ok=False, profile=inp.profile, error=ERROR_UNKNOWN_PROFILE, contract=CONTRACT_VERSION)
    profile, questionnaire, policy = resolved
    try:
        port, timeout_s = _oracle(profile)
        state = questionnaire.burst_state(inp.messages, pending=inp.pending, last_bot_text=inp.last_bot_text)
        result = await port.ask(state, questionnaire.burst_questions(inp.messages), timeout_s=timeout_s, redact=redact)
    except Exception as exc:  # noqa: BLE001 — fail-open: el turno sale como hoy
        return TurnDecisions(
            ok=False, profile=inp.profile, error=f"unexpected: {exc!r}"[:300], contract=CONTRACT_VERSION,
            versions=_versions(profile, ""),
        )
    plan = policy.plan_from_answers(
        result, topics=questionnaire.topic_ids, n_messages=len(inp.messages), thresholds=profile.thresholds
    )
    picked = {f"topic.{t.topic}" for t in plan.topics}
    return TurnDecisions(
        ok=plan.ok,
        profile=inp.profile,
        model=result.model,
        topics=policy.topic_rows(plan, questionnaire),
        stage=plan.stage,
        answers=_answers_for_trace(result, picked=picked),
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        contract=CONTRACT_VERSION,
        versions=_versions(profile, result.model),
        note=policy.checklist_note(plan, questionnaire),
        coverage=policy.coverage_rules(plan),
    )


def _plan_of_rows(rows: Sequence[dict]) -> TurnPlan:
    return TurnPlan(
        ok=True,
        topics=tuple(PlanTopic(str(t.get("topic")), t.get("msg"), t.get("p")) for t in rows if t.get("topic")),
    )


async def verify(inp: VerifyInput, *, redact: Sequence[str] = ()) -> VerifyOutput:
    """③ Antes de enviar: ¿la respuesta atiende cada asunto del plan?"""
    plan = _plan_of_rows(inp.topics)
    if not plan.topics:
        return VerifyOutput(ok=True, decision="send")
    resolved = _resolve(inp.profile)
    if resolved is None:
        return VerifyOutput(ok=False, decision="send", error=ERROR_UNKNOWN_PROFILE)
    profile, questionnaire, policy = resolved
    try:
        port, timeout_s = _oracle(profile)
        result = await port.ask(
            questionnaire.reply_state(inp.messages, inp.reply_text, inp.components),
            questionnaire.verify_questions(plan),
            timeout_s=timeout_s,
            redact=redact,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        return VerifyOutput(ok=False, decision="send", error=f"unexpected: {exc!r}"[:300])
    decision = policy.coverage_decision(plan, result, thresholds=profile.thresholds)
    covered_th = profile.thresholds.get("covered", 0.70)
    covered = {f"cover.{k}" for k, p in decision.covered.items() if p >= covered_th}
    return VerifyOutput(
        ok=result.ok,
        decision=decision.decision,
        missing=list(decision.missing),
        answers=_answers_for_trace(result, picked=covered),
        model=result.model,
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        complement_note=(
            policy.complement_note(plan, decision.missing, questionnaire) if decision.decision == "complement" else None
        ),
    )
