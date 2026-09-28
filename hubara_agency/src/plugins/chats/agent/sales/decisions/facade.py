"""Fachada del motor para el workflow de ventas: lo ÚNICO que el workflow
importa del motor (además de `contracts`). Un test de fronteras lo exige.

Todo acá es PURO y seguro dentro del workflow: aplica lo que el motor dejó
GRABADO en el resultado de sus activities (la nota, las reglas de la capa ②,
el texto del complemento). El workflow no conoce preguntas, umbrales ni
cuestionarios, y no lee archivos: cambiar una regla del motor afecta solo a
los turnos nuevos y nunca rompe el replay.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.activities import (
    PERCEPTION_ACTIVITIES as PERCEPTION_ACTIVITIES,
    perceive_burst_activity as perceive_burst_activity,
    verify_coverage_activity as verify_coverage_activity,
)
from src.plugins.chats.agent.sales.decisions.contracts import (
    EgressInput as EgressInput,
    EgressOutput as EgressOutput,
    PerceiveInput as PerceiveInput,
    PerceiveOutput as PerceiveOutput,
    TurnDecisions as TurnDecisions,
    VerifyInput as VerifyInput,
    VerifyOutput as VerifyOutput,
)
# Egreso del workflow V2 (F4): el workflow agenda la activity y aplica sus
# veredictos; las reglas y las preguntas a Jev viven en `egress.py`.
from src.plugins.chats.agent.sales.decisions.egress_activities import (
    EGRESS_ACTIVITIES as EGRESS_ACTIVITIES,
    decide_egress_activity as decide_egress_activity,
)
from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan, uncovered_topics
from src.sdk.agentkit import TurnPolicy

# Tools que tocan al cliente (envían o encolan algo que el flush entrega).
_CARD_PREFIXES = ("present_", "send_", "request_")
# `send_reply` no es una tarjeta: su texto ES la respuesta del turno.
_NOT_CARDS = frozenset({"send_reply"})


def plan_of(decisions: TurnDecisions) -> TurnPlan:
    return TurnPlan(
        ok=decisions.ok,
        topics=tuple(
            PlanTopic(str(t.get("topic")), t.get("msg"), t.get("p")) for t in decisions.topics if t.get("topic")
        ),
        stage=decisions.stage,
    )


def _labels(decisions: TurnDecisions) -> dict[str, str]:
    return {str(t.get("topic")): str(t.get("label") or t.get("topic")) for t in decisions.topics if t.get("topic")}


def _pending_round_note(labels: Sequence[str]) -> str:
    return (
        "[SISTEMA] Antes de terminar el turno: el cliente también preguntó por "
        + ", ".join(labels)
        + " y todavía no lo atendiste. Responde eso ahora con send_reply (breve)."
    )


def turn_policy_of(decisions: TurnDecisions) -> TurnPolicy | None:
    """Capa ②: una ronda más si el corte por tool deja un asunto sin atender
    con lo que el cliente ve. Sin reglas grabadas, el turno de hoy."""
    rules = decisions.coverage if isinstance(decisions.coverage, Mapping) else {}
    plan = plan_of(decisions)
    if not rules or not plan.topics:
        return None
    labels = _labels(decisions)

    def extra_round_note(tools_used: list[str], shown: str) -> str | None:
        missing = uncovered_topics(plan.topics, rules, tools_used=tools_used, shown=shown)
        return _pending_round_note([labels.get(t.topic, t.topic) for t in missing]) if missing else None

    return TurnPolicy(extra_round_note=extra_round_note)


def complement_note_of(decisions_topics: Sequence[dict], verify_out: VerifyOutput) -> str:
    """③ El turno de sistema del complemento: el que redactó el motor; si no
    viajó (resultado anterior al motor), uno con las etiquetas grabadas."""
    if verify_out.complement_note:
        return verify_out.complement_note
    labels = {str(t.get("topic")): str(t.get("label") or t.get("topic")) for t in decisions_topics if t.get("topic")}
    return (
        "[SISTEMA] Complemento del turno: el cliente también preguntó por "
        + ", ".join(labels.get(m, m) for m in verify_out.missing)
        + " y la respuesta que ya le enviaste no lo cubrió. Responde SOLO eso, breve, "
        "en un único mensaje con send_reply. No repitas lo que ya le enviaste."
    )


def _refused(result: Any) -> bool:
    """¿La tool dijo que no le mostró nada al cliente (`queued: false` o
    `error`)? Sin envelope legible se asume que salió (como el corte L-11)."""
    if not isinstance(result, str):
        return False
    try:
        payload = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and (payload.get("queued") is False or bool(payload.get("error")))


def delivered_components(tool_events: Sequence[Mapping[str, Any]]) -> list[str]:
    """③ Las tarjetas que el cliente recibe en el turno: tools que tocan al
    cliente, que no se negaron, sin `send_reply` (su texto ya es la
    respuesta). En orden y sin repetir."""
    out: list[str] = []
    for event in tool_events:
        name = str(event.get("name") or "")
        if not name.startswith(_CARD_PREFIXES) or name in _NOT_CARDS or name in out:
            continue
        if _refused(event.get("result")):
            continue
        out.append(name)
    return out
