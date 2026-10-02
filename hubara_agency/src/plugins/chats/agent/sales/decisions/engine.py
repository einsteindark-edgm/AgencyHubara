"""Núcleo del motor de decisiones: perfil → cuestionario → Jev → política.

Sin Temporal y sin I/O propio (salvo la llamada a Jev por el puerto del SDK):
lo que necesita del vault (qué tapar, el contexto del turno) se lo pasa quien
lo llama (las activities y, en F6, las guardas de las tools). Así el mismo
núcleo sirve a activities y tools, y el laboratorio lo corre igual.

* Sombra doble: si el perfil nombra un perfil en `shadow`, los dos le
  preguntan a Jev EN PARALELO dentro de la misma llamada; lo de la sombra solo
  va a la traza y tiene su propio tiempo máximo (`SHADOW_TIMEOUT_S`) para no
  demorar el turno.
* Calibración: si el perfil fija `calibrated_model` y Jev responde con otra
  versión, lo que actúa baja a sombra: sin nota ni reglas, con el motivo en
  `acting`. Se lee en la traza y en el panel «Bot nuevo».

NUNCA lanza: un perfil desconocido, Jev caído, tarde o con otra forma dan un
resultado vacío (`ok=False`) con el motivo, y el consumidor sigue como hoy.
"""
from __future__ import annotations

import asyncio
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
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnOutcome, TurnPlan
from src.plugins.chats.agent.sales.decisions.profiles import EngineProfile, get_engine_profile
from src.plugins.chats.agent.sales.decisions.questionnaire import Questionnaire
from src.plugins.chats.agent.sales.decisions.turn import Turn, turn_of
from src.sdk.decisionkit import BundleError

logger = structlog.get_logger()

ERROR_UNKNOWN_PROFILE = "unknown_profile"
#: Tiempo máximo de la sombra: corre en paralelo y no puede demorar el turno.
SHADOW_TIMEOUT_S = 1.5


def _resolve(profile_id: str) -> tuple[EngineProfile, Turn] | None:
    """El perfil y con qué corre su turno (su cuestionario y su política, o
    el turno del paquete activo: `decisions/turn.py`)."""
    resolved, _error = _resolve_or_error(profile_id)
    return resolved


def _resolve_or_error(profile_id: str) -> tuple[tuple[EngineProfile, Turn] | None, str]:
    """(lo resuelto, o None y el motivo para la traza): un perfil que no
    existe es `unknown_profile`; un paquete que no compila, `bundle_error: …`
    (no se disfraza de perfil desconocido)."""
    try:
        profile = get_engine_profile(profile_id)
        if profile is None:
            return None, ERROR_UNKNOWN_PROFILE
        return (profile, turn_of(profile)), ""
    except BundleError as exc:  # el paquete no compila: el turno sale como hoy
        logger.warning("decisions.bundle_error", profile=profile_id, error=str(exc)[:300])
        return None, f"bundle_error: {exc}"[:300]
    except KeyError as exc:  # perfil mal armado: el turno sale como hoy
        logger.warning("decisions.bad_profile", profile=profile_id, error=str(exc))
        return None, ERROR_UNKNOWN_PROFILE


def needs_context(profile_id: str) -> bool:
    """¿El perfil (o su sombra) lee el contexto del turno? Solo entonces la
    activity lo arma leyendo el vault."""
    ids = [profile_id]
    profile = get_engine_profile(profile_id)
    if profile is not None and profile.shadow:
        ids.append(profile.shadow)
    for pid in ids:
        resolved = _resolve(pid)
        if resolved is not None and resolved[1].questionnaire.uses_context:
            return True
    return False


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
    versions = {"profile": profile.id, "questions": profile.questions, "policy": profile.policy, "model": model}
    if profile.bundle is not None:  # el turno del paquete activo (F7)
        versions["bundle"] = profile.bundle
    return versions


def burst_request(questionnaire: Questionnaire, inp: PerceiveInput, context: Any = None) -> tuple[str, list[Any]]:
    """El `state` y las preguntas de la ráfaga, tal como se le mandan a Jev
    (sin anonimizar: eso lo hace el adaptador con `redact`). Lo usan el turno
    (`_ask`), la sonda diaria (`probe.py`) y el banco de referencia del
    laboratorio: los tres le preguntan exactamente lo mismo."""
    facts = context.when_facts() if context is not None and questionnaire.uses_context else {}
    state = questionnaire.burst_state(
        inp.messages,
        pending=inp.pending,
        last_bot_text=inp.last_bot_text,
        context=context if questionnaire.uses_context else None,
    )
    return state, questionnaire.burst_questions(inp.messages, facts=facts)


async def _ask(
    resolved: tuple[EngineProfile, Turn],
    inp: PerceiveInput,
    *,
    redact: Sequence[str],
    context: Any,
    timeout_s: float | None = None,
) -> Any:
    profile, turn = resolved
    port, oracle_timeout = _oracle(profile)
    state, questions = burst_request(turn.questionnaire, inp, context)
    return await port.ask(state, questions, timeout_s=timeout_s or oracle_timeout, redact=redact)


def _outcome(resolved: tuple[EngineProfile, Turn], result: Any, inp: PerceiveInput, context: Any) -> TurnOutcome:
    _profile, turn = resolved
    return turn.policy.decide_turn(
        result,
        questionnaire=turn.questionnaire,
        context=context if turn.questionnaire.uses_context else None,
        n_messages=len(inp.messages),
        thresholds=turn.thresholds,
        tables=turn.tables,
    )


def _acting(profile: EngineProfile, served_model: str) -> dict[str, Any]:
    if profile.calibrated_model and served_model and served_model != profile.calibrated_model:
        return {"allowed": False, "reason": "model_changed", "calibrated": profile.calibrated_model, "served": served_model}
    return {"allowed": True}


async def _shadow(
    shadow_id: str, inp: PerceiveInput, *, redact: Sequence[str], context: Any
) -> dict[str, Any]:
    resolved, error = _resolve_or_error(shadow_id)
    if resolved is None:
        return {"profile": shadow_id, "ok": False, "error": error}
    try:
        result = await asyncio.wait_for(
            _ask(resolved, inp, redact=redact, context=context, timeout_s=SHADOW_TIMEOUT_S), timeout=SHADOW_TIMEOUT_S + 0.25
        )
    except TimeoutError:
        return {"profile": shadow_id, "ok": False, "error": "timeout"}
    except Exception as exc:  # noqa: BLE001 — la sombra nunca tumba el turno
        return {"profile": shadow_id, "ok": False, "error": f"unexpected: {exc!r}"[:200]}
    if not result.ok:
        return {"profile": shadow_id, "ok": False, "error": result.error or "error", "model": result.model}
    outcome = _outcome(resolved, result, inp, context)
    return {
        "profile": shadow_id,
        "ok": True,
        "versions": _versions(resolved[0], result.model),
        "topics": outcome.topics,
        "reading": outcome.reading,
        "note": outcome.note,
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
    }


async def perceive(inp: PerceiveInput, *, redact: Sequence[str] = (), context: Any = None) -> TurnDecisions:
    """① Antes del turno: los asuntos de la ráfaga, la nota, las reglas de ② y
    la lectura del hilo (con el perfil en sombra al lado, si hay)."""
    resolved, error = _resolve_or_error(inp.profile)
    if resolved is None:
        return TurnDecisions(ok=False, profile=inp.profile, error=error, contract=CONTRACT_VERSION)
    profile = resolved[0]
    shadow_task = (
        asyncio.ensure_future(_shadow(profile.shadow, inp, redact=redact, context=context)) if profile.shadow else None
    )
    try:
        result = await _ask(resolved, inp, redact=redact, context=context)
    except Exception as exc:  # noqa: BLE001 — fail-open: el turno sale como hoy
        if shadow_task is not None:
            shadow_task.cancel()
        return TurnDecisions(
            ok=False, profile=inp.profile, error=f"unexpected: {exc!r}"[:300], contract=CONTRACT_VERSION,
            versions=_versions(profile, ""),
        )
    shadow = await shadow_task if shadow_task is not None else {}
    outcome = _outcome(resolved, result, inp, context)
    acting = _acting(profile, result.model) if result.ok else {}
    allowed = acting.get("allowed", True)
    picked = {f"topic.{t.topic}" for t in outcome.plan.topics}
    return TurnDecisions(
        ok=outcome.plan.ok,
        profile=inp.profile,
        model=result.model,
        topics=outcome.topics,
        stage=outcome.plan.stage,
        answers=_answers_for_trace(result, picked=picked),
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        contract=CONTRACT_VERSION,
        versions=_versions(profile, result.model),
        note=outcome.note if allowed else None,
        coverage=outcome.coverage if allowed else {},
        reading=outcome.reading,
        shadow=shadow,
        acting=acting,
        tools=outcome.tools if allowed else {},
        guide=outcome.guide,
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
    resolved, error = _resolve_or_error(inp.profile)
    if resolved is None:
        return VerifyOutput(ok=False, decision="send", error=error)
    profile, turn = resolved
    questionnaire = turn.questionnaire
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
    decision = turn.policy.coverage_decision(plan, result, thresholds=turn.thresholds, tables=turn.tables)
    if result.ok and not _acting(profile, result.model).get("allowed", True):
        # Otra versión de Jev que la calibrada: se mide, pero no actúa.
        decision = type(decision)(decision="send", covered=decision.covered)
    covered_th = turn.thresholds.get("covered", 0.70)
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
            turn.policy.complement_note(plan, decision.missing, questionnaire) if decision.decision == "complement" else None
        ),
    )
