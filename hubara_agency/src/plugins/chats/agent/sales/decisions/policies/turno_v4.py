"""Política `turno-v4`: la de `turno-v3` cuando el cliente escribe desde un
producto. PURA.

Conversación de prueba del 2026-10-07 (···1604, run 95f3f563, turno 3): tras
la lista de 4 productos de Halloween, el cliente tocó «Enviar mensaje a la
empresa» en la ficha de la Calabaza: «Me gusta esta». La nota del ingest llegó
(«…no le preguntes qué producto busca»), pero la etapa sale del borrador,
vacío, y `turno-v3` cerró el bloque con «Siguiente paso: ayúdale a escoger un
producto (catálogo o ficha).». El modelo obedeció: «¿Cuál de las cuatro te
gustó?».

Con el producto desde el que escribe (`TurnContext.viewed_product`: la ficha
del catálogo o la página de la web, resuelto por el ingest), el paso de la
etapa de descubrimiento es responderle sobre ese producto y, si lo quiere,
anotarlo. Todo lo demás es `turno-v3` (una prueba exige la paridad).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import TurnOutcome
from src.plugins.chats.agent.sales.decisions.policies import turno_v3
from src.plugins.chats.agent.sales.decisions.policies.tables import TurnTables

POLICY_ID = "turno-v4"

DEFAULT_THRESHOLDS = turno_v3.DEFAULT_THRESHOLDS

# Re-exporta lo que no cambia con respecto a v3.
coverage_decision = turno_v3.coverage_decision
complement_note = turno_v3.complement_note
coverage_rules = turno_v3.coverage_rules
topic_rows = turno_v3.topic_rows


def _where(viewed: Any) -> str:
    variant = f" ({viewed.variant})" if getattr(viewed, "variant", None) else ""
    name = f"«{viewed.title}{variant}»"
    if viewed.from_catalog:
        return f"el cliente escribe desde la ficha de {name} en el catálogo de WhatsApp"
    return f"el cliente llegó desde la página de {name} en la web"


def _card_step(viewed: Any) -> turno_v3.NextStep:
    def step(stage: str | None, *args: Any, **kwargs: Any) -> str:
        if stage == "etapa_descubrimiento":
            return (
                f"{_where(viewed)}: respóndele sobre ese producto, sin preguntarle cuál le gustó. "
                "Si lo quiere, guárdalo con set_order_slot y sigue con lo que falte."
            )
        return turno_v3._next_step(stage, *args, **kwargs)

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
        tables=tables, next_step=_card_step(viewed) if viewed is not None else None,
    )
    if viewed is not None and getattr(context, "stage", None) == "etapa_descubrimiento" and outcome.guide.get("next"):
        # La traza dice con qué producto se armó el paso.
        return replace(outcome, guide={**outcome.guide, "viewed_product": viewed.title})
    return outcome


__all__ = [
    "POLICY_ID",
    "complement_note",
    "coverage_decision",
    "coverage_rules",
    "decide_turn",
    "topic_rows",
]
