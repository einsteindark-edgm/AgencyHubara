"""El turno de un perfil del motor (PAQUETES_DE_DECISION.md F7).

Un perfil del motor nombra su cuestionario, su política y sus umbrales
(`jev-v1`…`jev-v4`), o toma el turno del paquete activo (`turn: bundle`,
`jev-v5`): el `turn.yaml` certificado del paquete de la tienda trae la ráfaga
①, la verificación ③ y las tablas de la política. Este es el único punto que
lo resuelve, para el motor (`engine`), la sonda diaria (`probe`) y el banco
de referencia del laboratorio: los tres le preguntan a Jev lo mismo.

Las tablas del paquete se le pasan a la política con la forma de sus
constantes (`policies/tables.py`): el contrato y ③ corren las filas CEL del
paquete; lo demás es la misma lógica de la política. PURO: sin Temporal ni
I/O de red (el paquete se lee una vez, con el registro).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from types import ModuleType
from typing import Any

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.decisions.plan import CoverageDecision
from src.plugins.chats.agent.sales.decisions.policies import get_policy
from src.plugins.chats.agent.sales.decisions.policies.tables import TurnTables
from src.plugins.chats.agent.sales.decisions.profiles import EngineProfile
from src.plugins.chats.agent.sales.decisions.questionnaire import Questionnaire, load_questionnaire, questionnaire_of
from src.sdk.decisionkit import CompiledTurn


@dataclass(frozen=True)
class Turn:
    """Con qué corre el turno un perfil."""

    questionnaire: Questionnaire
    policy: ModuleType
    thresholds: dict[str, float] = field(default_factory=dict)
    #: Las tablas del paquete; None = las constantes de la política.
    tables: TurnTables | None = None


def turn_of(profile: EngineProfile) -> Turn:
    """El turno del perfil: el del paquete activo (`turn: bundle`) o su
    cuestionario y su política. `KeyError` si un nombre no existe;
    `BundleError` si el paquete no compila."""
    if profile.bundle is None:
        return Turn(load_questionnaire(profile.questions), get_policy(profile.policy), dict(profile.thresholds))
    compiled = registry.active_turn()
    return Turn(
        questionnaire_of(compiled.questionnaire),
        get_policy(compiled.policy),
        compiled.thresholds,
        tables_of(compiled),
    )


def tables_of(compiled: CompiledTurn) -> TurnTables:
    """Las tablas del `turn.yaml` con la forma de las constantes de la política."""
    guide = compiled.guide
    return TurnTables(
        coverage={
            topic: (frozenset(rule["tools"]), tuple(rule["words"]), bool(rule["any_text"]))
            for topic, rule in compiled.coverage.items()
        },
        reading_notes=compiled.reading_answered,
        reading_any=compiled.reading_any,
        choice_topics=tuple(guide.choice_topics),
        no_sale_stages=tuple(guide.no_sale_stages),
        stagnant_turns=guide.stagnant_turns,
        slot_labels=dict(guide.slot_labels),
        contract=partial(_contract, compiled),
        verify=partial(_verify, compiled),
    )


def _first_answers(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for answer in getattr(result, "answers", ()) or ():
        qid = getattr(answer, "id", None)
        if isinstance(qid, str) and qid not in out:  # la primera, como `answer_of`
            out[qid] = answer
    return out


def _contract(compiled: CompiledTurn, topics: Sequence[str], result: Any, stage: str | None) -> list[dict[str, Any]]:
    # `p` como lo leía el código del contrato (`turno_v3._p`): una respuesta
    # sin probabilidad vale 0; sin respuesta, la pregunta no está en `p`.
    p = {
        qid: float(value) if isinstance(value := getattr(answer, "p", None), (int, float)) else 0.0
        for qid, answer in _first_answers(result).items()
    }
    return compiled.required(topics, p=p, inp={"stage": stage})


def _verify(compiled: CompiledTurn, plan: Any, result: Any) -> CoverageDecision:
    if not plan.topics or not getattr(result, "ok", False):
        return CoverageDecision(decision="send")  # sin verificación: se envía (fail-open)
    # Una respuesta sin probabilidad es duda (no está en `p`), como en el código.
    p = {
        qid: float(value)
        for qid, answer in _first_answers(result).items()
        if isinstance(value := getattr(answer, "p", None), (int, float))
    }
    out = compiled.verify([(t.topic, t.msg) for t in plan.topics], p=p)
    return CoverageDecision(decision=out.decision, missing=tuple(out.missing), covered=dict(out.covered))


__all__ = ["Turn", "tables_of", "turn_of"]
