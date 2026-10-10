"""Política `turno-v5`: la de `turno-v4` con el paso de post-venta cuando el
cliente manda o menciona el comprobante. PURA.

Caso de producción del 2026-10-09 (pedido #64): el humano vendió, «Confirmar
pago» le devolvió la conversación al bot y el cliente respondió citando su
comprobante. En post-venta, con `postcierre.comprobante`, `turno-v4` (el paso de
`turno-v3`) le decía al LLM «agradece y confirma que el equipo revisa el pago»,
y el pago ya estaba confirmado. La política no sabe si está pagado: el paso es
mirarlo con `check_order_status` y decir lo que diga. Todo lo demás es
`turno-v4` (una prueba exige la paridad).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import TurnOutcome
from src.plugins.chats.agent.sales.decisions.policies import turno_v3, turno_v4
from src.plugins.chats.agent.sales.decisions.policies.tables import TurnTables

POLICY_ID = "turno-v5"

DEFAULT_THRESHOLDS = turno_v4.DEFAULT_THRESHOLDS

# Re-exporta lo que no cambia con respecto a v4.
coverage_decision = turno_v4.coverage_decision
complement_note = turno_v4.complement_note
coverage_rules = turno_v4.coverage_rules
topic_rows = turno_v4.topic_rows

#: El paso de `turno-v3`/`turno-v4` para el comprobante en post-venta.
_TEAM_CHECKS_STEP = "agradece y confirma que el equipo revisa el pago."
RECEIPT_STEP = (
    "mira el pago con check_order_status: si ya está pagado, dile que su pago quedó confirmado; "
    "si no, agradécele y dile que el equipo lo está verificando."
)


def _step(viewed: Any) -> turno_v3.NextStep:
    base = turno_v4._card_step(viewed) if viewed is not None else turno_v3._next_step

    def step(stage: str | None, *args: Any, **kwargs: Any) -> str:
        text = base(stage, *args, **kwargs)
        return RECEIPT_STEP if stage == "etapa_postcierre" and text == _TEAM_CHECKS_STEP else text

    return step


def decide_turn(
    result: Any,
    *,
    questionnaire: Any,
    context: Any = None,
    n_messages: int,
    thresholds: dict[str, float] | None = None,
    tables: TurnTables | None = None,
) -> TurnOutcome:
    viewed = getattr(context, "viewed_product", None)
    outcome = turno_v3.decide_turn_with(
        result, questionnaire=questionnaire, context=context, n_messages=n_messages, thresholds=thresholds,
        tables=tables, next_step=_step(viewed),
    )
    if viewed is not None and getattr(context, "stage", None) == "etapa_descubrimiento" and outcome.guide.get("next"):
        # La traza dice con qué producto se armó el paso (como `turno-v4`).
        return replace(outcome, guide={**outcome.guide, "viewed_product": viewed.title})
    return outcome


__all__ = [
    "POLICY_ID",
    "RECEIPT_STEP",
    "complement_note",
    "coverage_decision",
    "coverage_rules",
    "decide_turn",
    "topic_rows",
]
