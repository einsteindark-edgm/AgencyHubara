"""Política `turno-v6`: la de `turno-v5` con el producto que el cliente señala
en la etapa de descubrimiento. PURA.

Caso de producción del 2026-10-13 (anuncio de Halloween, turno 5): el cliente
citó la línea del set («…trae las tres juntas, con esas mismas medidas») y
escribió «Estás» (= «estas»). La etapa sale del borrador, vacío, y `turno-v5`
cerró el bloque con «Siguiente paso: ayúdale a escoger un producto (catálogo o
ficha).» más el refuerzo de tres turnos sin un dato nuevo: el modelo volvió a
preguntar «¿una sola o el set?».

Con la pregunta `descubrimiento.senala_producto` (≥ `pointed`), el paso es
hablarle de ese producto y preguntarle si se lo lleva, sin volver a preguntar
cuál. Que le guste no es que lo quiera (criterio del operador, 2026-10-07): el
paso pregunta; se anota cuando el cliente dice que sí. Pesa más que la ficha
desde la que escribe (`turno-v4`): lo que señala en este turno es más reciente.
Todo lo demás es `turno-v5` (una prueba exige la paridad).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import TurnOutcome
from src.plugins.chats.agent.sales.decisions.policies import turno_v3, turno_v5
from src.plugins.chats.agent.sales.decisions.policies.tables import TurnTables

POLICY_ID = "turno-v6"

#: `pointed`: el cliente señala un producto. Más bajo que `given`: con cita,
#: «Estás» (= «estas») le da a Jev 0,74–0,76 (sonda del 2026-10-13).
DEFAULT_THRESHOLDS: dict[str, float] = {**turno_v5.DEFAULT_THRESHOLDS, "pointed": 0.60}

# Re-exporta lo que no cambia con respecto a v5.
coverage_decision = turno_v5.coverage_decision
complement_note = turno_v5.complement_note
coverage_rules = turno_v5.coverage_rules
topic_rows = turno_v5.topic_rows

#: La pregunta del cuestionario que lee esta política (solo en descubrimiento).
POINTED_QUESTION = "descubrimiento.senala_producto"
POINTED_STEP = (
    "el cliente señaló qué producto le interesa (si citó un mensaje, es el que nombra ese mensaje; "
    "si nombró varios, son esos): no le vuelvas a preguntar cuál. Háblale de ese producto y "
    "pregúntale si se lo lleva; cuando te diga que sí, guárdalo con set_order_slot y sigue con lo que falte."
)


def _pointed_step(stage: str | None, *args: Any, **kwargs: Any) -> str:
    return POINTED_STEP if stage == "etapa_descubrimiento" else turno_v3._next_step(stage, *args, **kwargs)


def decide_turn(
    result: Any,
    *,
    questionnaire: Any,
    context: Any = None,
    n_messages: int,
    thresholds: dict[str, float] | None = None,
    tables: TurnTables | None = None,
) -> TurnOutcome:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    pointed = (
        getattr(context, "stage", None) == "etapa_descubrimiento"
        and turno_v3._p(result, POINTED_QUESTION) >= th["pointed"]
    )
    if not pointed:
        return turno_v5.decide_turn(
            result, questionnaire=questionnaire, context=context, n_messages=n_messages, thresholds=thresholds,
            tables=tables,
        )
    outcome = turno_v3.decide_turn_with(
        result, questionnaire=questionnaire, context=context, n_messages=n_messages, thresholds=thresholds,
        tables=tables, next_step=_pointed_step,
    )
    if not outcome.guide.get("next"):
        return outcome
    # La traza dice que el paso salió del producto señalado.
    return replace(outcome, guide={**outcome.guide, "pointed_product": True})


__all__ = [
    "POINTED_QUESTION",
    "POINTED_STEP",
    "POLICY_ID",
    "complement_note",
    "coverage_decision",
    "coverage_rules",
    "decide_turn",
    "topic_rows",
]
