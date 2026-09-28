"""Política `turno-v1`: de las respuestas de Jev al plan del turno. PURA.

Es la política de las capas ①②③ del laboratorio (PR 14), movida al motor:

  ① `plan_from_answers`: los asuntos que el cliente planteó (probabilidad
     ≥ `detect`), con el mensaje de cada uno. `checklist_note` es la nota que
     el LLM recibe. `coverage_rules` son las reglas de la capa ②, que viajan
     grabadas en el resultado de la activity (el workflow solo las aplica).
  ③ `coverage_decision`: `send` (todo atendido o sin verificación: fail-open),
     `complement` (falta algo con claridad: una burbuja más, como turno de
     sistema) o `pending` (duda: se envía y el asunto queda pendiente).
     `complement_note` es el texto de ese turno de sistema.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import (
    CoverageDecision,
    PlanTopic,
    TurnOutcome,
    TurnPlan,
    answer_of,
)

POLICY_ID = "turno-v1"

#: Los mismos umbrales que el perfil `jev-v1` (un test los mantiene iguales).
DEFAULT_THRESHOLDS: dict[str, float] = {"detect": 0.70, "confidence": 0.60, "covered": 0.70}

# ② Qué atiende cada asunto dentro del turno: tools que lo atienden, palabras
# del texto que el cliente ve (sin tildes) y si cualquier texto lo atiende.
# Conservador: sin nada de esto, el asunto cuenta como no atendido y el turno
# tiene una ronda más. `aplaza` pide una frase (antes, la palabra vacía lo
# daba siempre por cubierto: bug corregido el 2026-09-28).
_COVERAGE: dict[str, tuple[frozenset[str], tuple[str, ...], bool]] = {
    "catalogo": (frozenset({"present_products", "present_product_gallery", "present_product_detail", "list_categories"}), ("catalogo", "disenos", "modelos"), False),
    "precio": (frozenset({"present_products", "present_product_detail", "search_products", "get_product_by_handle"}), ("$", "precio", "vale", "cuesta"), False),
    "envio": (frozenset({"send_shipping_rates", "request_shipping_details"}), ("envio", "domicilio"), False),
    "tiempos": (frozenset(), ("dias", "habiles", "llega", "entrega", "listo"), False),
    "pagos": (frozenset(), ("nequi", "transferencia", "pago", "contra entrega", "contraentrega"), False),
    "medidas": (frozenset({"present_product_detail"}), ("cm", "medida", "tamano", "alto", "ancho"), False),
    "variante": (frozenset({"present_variant_picker"}), ("color",), False),
    "aroma": (frozenset({"present_variant_picker"}), ("aroma",), False),
    "personalizacion": (frozenset(), ("personaliz", "dedicatoria", "nombre", "grabad"), False),
    "disponibilidad": (frozenset({"search_products", "get_product_by_handle", "present_products"}), ("disponible", "stock", "agotad"), False),
    "estado_pedido": (frozenset({"check_order_status"}), ("pedido",), False),
    "datos_envio": (frozenset({"set_order_slot", "request_shipping_details", "present_order_confirmation"}), (), False),
    "confirma_compra": (frozenset({"present_order_confirmation", "verify_order_for_checkout", "register_order"}), (), False),
    "aplaza": (frozenset(), (), True),
    "queja": (frozenset({"escalate_to_human"}), ("disculp", "lament", "sentimos"), False),
    "foto": (frozenset({"present_product_detail", "search_products"}), ("foto",), False),
    "saludo": (frozenset(), ("hola", "buen", "bienvenid"), False),
}


def plan_from_answers(
    result: Any,
    *,
    topics: Sequence[str],
    n_messages: int,
    thresholds: dict[str, float] | None = None,
) -> TurnPlan:
    """① Los asuntos de la ráfaga, en el orden del cuestionario."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if not getattr(result, "ok", False):
        return TurnPlan(ok=False, error=getattr(result, "error", None))
    main_topic: dict[str, int] = {}
    for k in range(1, n_messages + 1):
        a = answer_of(result, f"msg.{k}.topic")
        if a is not None and getattr(a, "choice", None) and a.choice not in main_topic:
            main_topic[a.choice] = k
    found = []
    for topic in topics:
        p = getattr(answer_of(result, f"topic.{topic}"), "p", None)
        if isinstance(p, (int, float)) and p >= th["detect"]:
            found.append(PlanTopic(topic=topic, msg=main_topic.get(topic), p=float(p)))
    stage = getattr(answer_of(result, "stage"), "choice", None)
    return TurnPlan(ok=True, topics=tuple(found), stage=stage)


def _label(t: PlanTopic, questionnaire: Any) -> str:
    label = questionnaire.label(t.topic)
    return f"{label} (mensaje {t.msg})" if t.msg else label


def topic_rows(plan: TurnPlan, questionnaire: Any) -> list[dict[str, Any]]:
    """Los asuntos como viajan en `TurnDecisions.topics`, con la etiqueta que
    el workflow escribe en sus notas (no lee el cuestionario: es I/O)."""
    return [{"topic": t.topic, "msg": t.msg, "p": t.p, "label": _label(t, questionnaire)} for t in plan.topics]


def checklist_note(plan: TurnPlan, questionnaire: Any) -> str | None:
    """① La nota del turno: el LLM responde cada asunto de la ráfaga."""
    if not plan.ok or not plan.topics:
        return None
    items = "; ".join(f"{i}) {_label(t, questionnaire)}" for i, t in enumerate(plan.topics, 1))
    return (
        "[PLAN DEL TURNO] En su(s) mensaje(s) el cliente planteó estos asuntos: "
        f"{items}. Atiende cada uno en este turno; si una herramienta responde uno, "
        "responde los demás con send_reply."
    )


def coverage_rules(plan: TurnPlan) -> dict[str, dict[str, Any]]:
    """② Las reglas de los asuntos del plan, en JSON (viajan grabadas)."""
    rules: dict[str, dict[str, Any]] = {}
    for t in plan.topics:
        tools, words, any_text = _COVERAGE.get(t.topic, (frozenset(), (), False))
        rules[t.topic] = {"tools": sorted(tools), "words": list(words), "any_text": any_text}
    return rules


def decide_turn(
    result: Any,
    *,
    questionnaire: Any,
    context: Any = None,
    n_messages: int,
    thresholds: dict[str, float] | None = None,
) -> TurnOutcome:
    """① Todo lo que el turno recibe del motor. v1 no usa contexto."""
    plan = plan_from_answers(result, topics=questionnaire.topic_ids, n_messages=n_messages, thresholds=thresholds)
    return TurnOutcome(
        plan=plan,
        topics=topic_rows(plan, questionnaire),
        note=checklist_note(plan, questionnaire),
        coverage=coverage_rules(plan),
    )


def coverage_decision(plan: TurnPlan, result: Any, *, thresholds: dict[str, float] | None = None) -> CoverageDecision:
    """③ send | complement | pending, con la probabilidad de cada asunto."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if not plan.topics or not getattr(result, "ok", False):
        return CoverageDecision(decision="send")
    covered: dict[str, float] = {}
    clear_miss: list[str] = []
    unsure: list[str] = []
    for t in plan.topics:
        p = getattr(answer_of(result, f"cover.{t.topic}"), "p", None)
        if not isinstance(p, (int, float)):
            unsure.append(t.topic)
            continue
        covered[t.topic] = float(p)
        if p >= th["covered"]:
            continue
        (clear_miss if p <= 1 - th["covered"] else unsure).append(t.topic)
    if clear_miss:
        return CoverageDecision(decision="complement", missing=tuple(clear_miss), covered=covered)
    if unsure:
        return CoverageDecision(decision="pending", missing=tuple(unsure), covered=covered)
    return CoverageDecision(decision="send", covered=covered)


def complement_note(plan: TurnPlan, missing: Sequence[str], questionnaire: Any) -> str:
    """③ El turno de sistema del complemento: UNA burbuja con lo que faltó."""
    wanted = set(missing)
    topics = [t for t in plan.topics if t.topic in wanted] or [PlanTopic(m) for m in missing]
    return (
        "[SISTEMA] Complemento del turno: el cliente también preguntó por "
        + ", ".join(_label(t, questionnaire) for t in topics)
        + " y la respuesta que ya le enviaste no lo cubrió. Responde SOLO eso, breve, "
        "en un único mensaje con send_reply. No repitas lo que ya le enviaste."
    )
