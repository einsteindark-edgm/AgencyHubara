"""Plan del turno con clasificador (plan del laboratorio §3.2). PURO y seguro
dentro del workflow: no importa el puerto ni hace I/O; trabaja sobre
respuestas con `id`, `p` y `choice`.

  ① `plan_from_answers`: los asuntos que el cliente planteó (probabilidad
     ≥ `detect`), con el mensaje de cada uno (asunto principal del mensaje).
     `checklist_note` es la nota que el LLM recibe: responder cada asunto.
  ② `uncovered_topics`: regla determinista para la política del turno. Si
     el corte por tool deja un asunto del plan sin ninguna tool ni palabra
     que lo atienda, `run_agent_turn` da UNA ronda más. No llama a nadie.
  ③ `coverage_decision`: con la verificación del clasificador, `send` (todo
     atendido o sin verificación: fail-open), `complement` (falta algo con
     claridad: una burbuja más, como turno de sistema) o `pending` (duda: se
     envía y el asunto queda pendiente para el turno siguiente).
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Mismos umbrales que los perfiles versionados (`profiles.yaml`); un test
#: los mantiene iguales.
DEFAULT_THRESHOLDS: dict[str, float] = {"detect": 0.70, "confidence": 0.60, "covered": 0.70}

TOPIC_LABELS: dict[str, str] = {
    "catalogo": "catálogo",
    "precio": "precio",
    "envio": "envío",
    "tiempos": "tiempos de entrega",
    "pagos": "medios de pago",
    "medidas": "medidas",
    "variante": "colores o variantes",
    "aroma": "aromas",
    "personalizacion": "personalización",
    "disponibilidad": "disponibilidad",
    "estado_pedido": "estado del pedido",
    "datos_envio": "datos de envío",
    "confirma_compra": "confirmación de compra",
    "aplaza": "aplazamiento",
    "queja": "queja",
    "foto": "la foto",
    "saludo": "saludo",
}

# ② Qué atiende cada asunto dentro del turno: tools que lo cubren y palabras
# del texto (sin tildes). Conservador: sin nada de esto, el asunto cuenta
# como no atendido y el turno tiene una ronda más.
_COVERED_BY: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "catalogo": (frozenset({"present_products", "present_product_gallery", "present_product_detail", "list_categories"}), ("catalogo", "disenos", "modelos")),
    "precio": (frozenset({"present_products", "present_product_detail", "search_products", "get_product_by_handle"}), ("$", "precio", "vale", "cuesta")),
    "envio": (frozenset({"send_shipping_rates", "request_shipping_details"}), ("envio", "domicilio")),
    "tiempos": (frozenset(), ("dias", "habiles", "llega", "entrega", "listo")),
    "pagos": (frozenset(), ("nequi", "transferencia", "pago", "contra entrega", "contraentrega")),
    "medidas": (frozenset({"present_product_detail"}), ("cm", "medida", "tamano", "alto", "ancho")),
    "variante": (frozenset({"present_variant_picker"}), ("color",)),
    "aroma": (frozenset({"present_variant_picker"}), ("aroma",)),
    "personalizacion": (frozenset(), ("personaliz", "dedicatoria", "nombre", "grabad")),
    "disponibilidad": (frozenset({"search_products", "get_product_by_handle", "present_products"}), ("disponible", "stock", "agotad")),
    "estado_pedido": (frozenset({"check_order_status"}), ("pedido",)),
    "datos_envio": (frozenset({"set_order_slot", "request_shipping_details", "present_order_confirmation"}), ()),
    "confirma_compra": (frozenset({"present_order_confirmation", "verify_order_for_checkout", "register_order"}), ()),
    "aplaza": (frozenset(), ("",)),
    "queja": (frozenset({"escalate_to_human"}), ("disculp", "lament", "sentimos")),
    "foto": (frozenset({"present_product_detail", "search_products"}), ("foto",)),
    "saludo": (frozenset(), ("hola", "buen", "bienvenid")),
}


@dataclass(frozen=True)
class PlanTopic:
    topic: str
    msg: int | None = None
    p: float | None = None


@dataclass(frozen=True)
class TurnPlan:
    ok: bool
    topics: tuple[PlanTopic, ...] = ()
    stage: str | None = None
    error: str | None = None

    def labels(self) -> list[str]:
        return [t.topic for t in self.topics]


@dataclass(frozen=True)
class CoverageDecision:
    decision: str  # send | complement | pending
    missing: tuple[str, ...] = ()
    covered: dict[str, float] = field(default_factory=dict)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


def _answer(result: Any, qid: str) -> Any:
    return next((a for a in getattr(result, "answers", ()) or () if getattr(a, "id", None) == qid), None)


def plan_from_answers(result: Any, *, n_messages: int, thresholds: dict[str, float] | None = None) -> TurnPlan:
    """① Los asuntos de la ráfaga, en el orden del juego de preguntas."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if not getattr(result, "ok", False):
        return TurnPlan(ok=False, error=getattr(result, "error", None))
    main_topic = {}
    for k in range(1, n_messages + 1):
        a = _answer(result, f"msg.{k}.topic")
        if a is not None and getattr(a, "choice", None) and a.choice not in main_topic:
            main_topic[a.choice] = k
    topics = []
    for topic in TOPIC_LABELS:
        a = _answer(result, f"topic.{topic}")
        p = getattr(a, "p", None)
        if isinstance(p, (int, float)) and p >= th["detect"]:
            topics.append(PlanTopic(topic=topic, msg=main_topic.get(topic), p=float(p)))
    stage = getattr(_answer(result, "stage"), "choice", None)
    return TurnPlan(ok=True, topics=tuple(topics), stage=stage)


def _label(t: PlanTopic) -> str:
    label = TOPIC_LABELS.get(t.topic, t.topic)
    return f"{label} (mensaje {t.msg})" if t.msg else label


def checklist_note(plan: TurnPlan) -> str | None:
    """① La nota del turno: el LLM responde cada asunto de la ráfaga."""
    if not plan.ok or not plan.topics:
        return None
    items = "; ".join(f"{i}) {_label(t)}" for i, t in enumerate(plan.topics, 1))
    return (
        "[PLAN DEL TURNO] En su(s) mensaje(s) el cliente planteó estos asuntos: "
        f"{items}. Atiende cada uno en este turno; si una herramienta responde uno, "
        "responde los demás con send_reply."
    )


def uncovered_topics(plan: TurnPlan, *, tools_used: Iterable[str], text: str) -> list[PlanTopic]:
    """② Asuntos del plan que ninguna tool ni palabra del texto atiende."""
    used = set(tools_used)
    plain = _plain(text)
    missing = []
    for t in plan.topics:
        tools, words = _COVERED_BY.get(t.topic, (frozenset(), ()))
        by_tool = bool(used & tools)
        by_text = bool(plain) and any(w in plain for w in words)
        if not (by_tool or by_text):
            missing.append(t)
    return missing


def pending_round_note(missing: Sequence[PlanTopic]) -> str:
    return (
        "[SISTEMA] Antes de terminar el turno: el cliente también preguntó por "
        + ", ".join(_label(t) for t in missing)
        + " y todavía no lo atendiste. Responde eso ahora con send_reply (breve)."
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
        p = getattr(_answer(result, f"cover.{t.topic}"), "p", None)
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


def complement_message(plan: TurnPlan, missing: Sequence[str]) -> str:
    """③ El turno de sistema del complemento: UNA burbuja con lo que faltó."""
    topics = [t for t in plan.topics if t.topic in set(missing)] or [PlanTopic(m) for m in missing]
    return (
        "[SISTEMA] Complemento del turno: el cliente también preguntó por "
        + ", ".join(_label(t) for t in topics)
        + " y la respuesta que ya le enviaste no lo cubrió. Responde SOLO eso, breve, "
        "en un único mensaje con send_reply. No repitas lo que ya le enviaste."
    )
