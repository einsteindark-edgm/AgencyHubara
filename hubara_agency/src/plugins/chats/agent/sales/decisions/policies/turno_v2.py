"""Política `turno-v2`: el «Si» con contexto (diseño v2 §01, fase F1). PURA.

Lo mismo que `turno-v1` (asuntos, reglas de la capa ②, verificación ③) más la
LECTURA del hilo: qué le preguntó el asesor al cliente (lo sabe el código si
lo último que vio fue la tarjeta de confirmación o el formulario; si no, lo
dice Jev), si el cliente lo responde y qué responde. Con eso:

* la nota le dice al LLM qué respondió el cliente («el Si confirma el dato de
  envío que le preguntaste; no es una compra nueva»);
* la evidencia de compra (`reading.purchase`): `si` solo con la pregunta de
  compra a la vista y un sí claro (p ≥ `purchase_confirm`); `no` solo si Jev
  está casi seguro (p ≤ `purchase_retract`) de que el «sí» respondía otra
  cosa; lo demás es `duda` y decide la regla de hoy. Actuar con esa evidencia
  (retirar la marca de compra) es de la fase F6.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import TurnOutcome, TurnPlan, answer_of
from src.plugins.chats.agent.sales.decisions.policies import turno_v1
from src.plugins.chats.agent.sales.decisions.policies.tables import TurnTables

POLICY_ID = "turno-v2"

DEFAULT_THRESHOLDS: dict[str, float] = {
    **turno_v1.DEFAULT_THRESHOLDS,
    "answers": 0.70,
    "purchase_confirm": 0.85,
    "purchase_retract": 0.20,
}

# Lo que responde el cliente, en la nota, según lo que se le preguntó.
_READING_NOTES: dict[tuple[str, str], str] = {
    ("confirmar_compra", "si"): "El cliente responde que SÍ a tu pregunta de compra: confirma el pedido.",
    ("confirmar_compra", "no"): "El cliente responde que NO a tu pregunta de compra: todavía no confirma el pedido.",
    ("confirmar_dato_envio", "si"): (
        "El cliente responde que sí a tu pregunta sobre un dato de envío: confirma ese dato. No es una compra nueva."
    ),
    ("confirmar_dato_envio", "no"): "El cliente responde que no al dato de envío que le preguntaste: pídele el dato correcto.",
    ("ver_opciones", "si"): "El cliente quiere ver las opciones que le ofreciste: muéstraselas con la herramienta que corresponde.",
    ("ver_opciones", "no"): "El cliente no quiere ver esas opciones.",
    ("otra_si_no", "si"): "El cliente responde que sí a tu última pregunta.",
    ("otra_si_no", "no"): "El cliente responde que no a tu última pregunta.",
}
_READING_ANY: dict[str, str] = {
    "elegir_variante": "El cliente responde tu pregunta de variantes (color, aroma, diseño o cantidad).",
    "pregunta_abierta": "El cliente responde tu última pregunta.",
}

# Re-exporta lo que no cambia con respecto a v1.
coverage_decision = turno_v1.coverage_decision
complement_note = turno_v1.complement_note
coverage_rules = turno_v1.coverage_rules
topic_rows = turno_v1.topic_rows


def _dist(answer: Any) -> dict[str, float]:
    probs = dict(getattr(answer, "probs", ()) or ())
    if not probs and getattr(answer, "choice", None):
        probs = {answer.choice: float(answer.confidence) if answer.confidence is not None else 1.0}
    return probs


def reading_from(result: Any, context: Any, thresholds: dict[str, float]) -> dict[str, Any]:
    """La lectura del hilo, o `{}` sin contexto o sin Jev."""
    window = getattr(context, "window", None)
    if not getattr(result, "ok", False) or window is None or not window.lines:
        return {}
    th = {**DEFAULT_THRESHOLDS, **thresholds}
    known = window.bot_asked_known
    if known:
        bot_asked, by, bot_asked_p = known, "codigo", 1.0
        p_purchase_question = 1.0 if known == "confirmar_compra" else 0.0
    else:
        asked = answer_of(result, "thread.bot_asked")
        if asked is None or not getattr(asked, "choice", None):
            return {}
        dist = _dist(asked)
        bot_asked, by, bot_asked_p = asked.choice, "jev", dist.get(asked.choice)
        p_purchase_question = dist.get("confirmar_compra", 0.0)
    answers_p = getattr(answer_of(result, "thread.answers_bot"), "p", None)
    answer_a = answer_of(result, "thread.answer")
    answer = getattr(answer_a, "choice", None)
    answer_p = _dist(answer_a).get(answer) if answer_a is not None and answer else None
    responds = isinstance(answers_p, (int, float)) and answers_p >= th["answers"]
    if responds and answer == "si" and (answer_p or 0.0) >= th["purchase_confirm"] and p_purchase_question >= th["purchase_confirm"]:
        purchase = "si"
    elif responds and answer == "si" and p_purchase_question <= th["purchase_retract"]:
        purchase = "no"
    else:
        purchase = "duda"
    return {
        "bot_asked": bot_asked,
        "bot_asked_by": by,
        "bot_asked_p": bot_asked_p,
        "answers_bot": answers_p,
        "answer": answer,
        "answer_p": answer_p,
        "purchase": purchase,
    }


def reading_sentence(reading: dict[str, Any], thresholds: dict[str, float], tables: TurnTables | None = None) -> str | None:
    th = {**DEFAULT_THRESHOLDS, **thresholds}
    answers_p = reading.get("answers_bot")
    if not reading or not isinstance(answers_p, (int, float)) or answers_p < th["answers"]:
        return None
    notes = _READING_NOTES if tables is None else tables.reading_notes
    any_answer = _READING_ANY if tables is None else tables.reading_any
    bot_asked, answer = str(reading.get("bot_asked") or ""), str(reading.get("answer") or "")
    return notes.get((bot_asked, answer)) or any_answer.get(bot_asked)


def _checklist_body(plan: TurnPlan, questionnaire: Any) -> str | None:
    if not plan.ok or not plan.topics:
        return None
    items = "; ".join(f"{i}) {row['label']}" for i, row in enumerate(topic_rows(plan, questionnaire), 1))
    return (
        f"En su(s) mensaje(s) de este turno el cliente planteó estos asuntos: {items}. Atiende cada uno en este "
        "turno; si una herramienta responde uno, responde los demás con send_reply."
    )


def reading_note(
    plan: TurnPlan, reading: dict[str, Any], questionnaire: Any, thresholds: dict[str, float],
    tables: TurnTables | None = None,
) -> str | None:
    """La nota del turno: qué responde el cliente y los asuntos que planteó."""
    parts = [p for p in (reading_sentence(reading, thresholds, tables), _checklist_body(plan, questionnaire)) if p]
    return ("[LECTURA DEL TURNO] " + " ".join(parts)) if parts else None


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
    plan = turno_v1.plan_from_answers(result, topics=questionnaire.topic_ids, n_messages=n_messages, thresholds=th)
    if not plan.ok:
        return TurnOutcome(plan=plan)
    reading = reading_from(result, context, th)
    return TurnOutcome(
        plan=plan,
        topics=topic_rows(plan, questionnaire),
        note=reading_note(plan, reading, questionnaire, th, tables),
        coverage=coverage_rules(plan, tables),
        reading=reading,
    )
