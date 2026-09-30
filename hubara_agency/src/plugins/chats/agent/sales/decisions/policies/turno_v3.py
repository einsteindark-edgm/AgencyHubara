"""Política `turno-v3`: contrato de herramientas y guía de etapas (diseño v2
§04 y §05, fase F6). PURA.

Lo de `turno-v2` (asuntos, lectura del «sí», reglas de ②, verificación ③)
más:

* Contrato de herramientas: Jev dice qué pide el cliente; este contrato
  escrito en código dice qué tool lo resuelve. Las requeridas viajan
  GRABADAS (`tools.required`, con su nota) para la segunda puerta del turno
  (una ronda extra que nombra la tool que falta) y la auditoría antes de
  enviar. Excepciones del diseño: el precio ya visto no pide tool; colores y
  aromas piden el selector solo en la etapa de variantes (antes basta la
  ficha); una queja pide un humano solo si reclama por un pedido ya hecho;
  los datos que el cliente dio en este turno piden `set_order_slot` con esos
  campos.
* Guía de etapas: la etapa la calcula el código (borrador); Jev aporta la
  evidencia del turno y el motor le da al LLM el siguiente paso concreto,
  acompaña al cliente cuando se devuelve (`quitar=true`) y refuerza el paso
  cuando lleva tres turnos sin un dato nuevo.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.context import SHIPPING_SLOTS, STAGE_LABELS
from src.plugins.chats.agent.sales.decisions.plan import TurnOutcome, answer_of
from src.plugins.chats.agent.sales.decisions.policies import turno_v2

POLICY_ID = "turno-v3"

DEFAULT_THRESHOLDS: dict[str, float] = {**turno_v2.DEFAULT_THRESHOLDS, "given": 0.85}

STAGNANT_TURNS = 3

_SLOT_LABELS: dict[str, str] = {
    "ciudad": "ciudad",
    "direccion": "dirección",
    "telefono": "teléfono",
    "nombre_recibe": "quien recibe",
    "metodo_pago": "método de pago",
}

#: asunto → (tools que lo resuelven, nota). `variante`/`aroma` dependen de la
#: etapa y `datos_envio`/`queja` de la evidencia: se arman aparte.
_CONTRACT: dict[str, tuple[tuple[str, ...], str]] = {
    "catalogo": (("present_products", "present_product_gallery", "list_categories"),
                 "Para mostrarle el catálogo usa present_products (o list_categories)."),
    "precio": (("search_products", "get_product_by_handle", "present_product_detail", "present_products"),
               "El precio sale del catálogo: consúltalo con search_products o get_product_by_handle antes de darlo."),
    "envio": (("send_shipping_rates",), "Para el costo del envío usa send_shipping_rates."),
    # `search_products` ya trae las medidas de cada producto (2026-09-29).
    "medidas": (("present_product_detail", "get_product_by_handle", "search_products"),
                "Las medidas salen del catálogo: consúltalas con search_products o get_product_by_handle."),
    "disponibilidad": (("search_products", "get_product_by_handle"),
                       "La disponibilidad sale del catálogo: consúltala con search_products o get_product_by_handle."),
    "estado_pedido": (("check_order_status",), "Para el estado del pedido usa check_order_status."),
    "promocion": (("list_promotions", "apply_coupon"),
                  "Para promociones o cupones usa list_promotions (o apply_coupon si ya dio el código)."),
}

# Re-exporta lo que no cambia con respecto a v2.
coverage_decision = turno_v2.coverage_decision
complement_note = turno_v2.complement_note
coverage_rules = turno_v2.coverage_rules
topic_rows = turno_v2.topic_rows


def _p(result: Any, qid: str) -> float:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else 0.0


def _required(topics: Sequence[str], result: Any, stage: str | None, given: list[str], th: dict[str, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for topic in topics:
        if topic == "precio" and _p(result, "precio.en_contexto") >= th["given"]:
            continue
        if topic in ("variante", "aroma"):
            if stage == "etapa_variantes":
                tools, nudge = ("present_variant_picker",), "Para que escoja color o aroma usa present_variant_picker."
            else:
                tools, nudge = (("present_product_detail", "get_product_by_handle"),
                                "Los colores y aromas salen de la ficha: usa present_product_detail.")
            out.append({"topic": topic, "any_of": list(tools), "nudge": nudge})
            continue
        if topic == "queja":
            if _p(result, "queja.pedido_hecho") >= th["given"]:
                out.append({"topic": "queja", "any_of": ["escalate_to_human"],
                            "nudge": "Es un reclamo sobre un pedido ya hecho: escala con escalate_to_human."})
            continue
        if topic in _CONTRACT:
            tools, nudge = _CONTRACT[topic]
            out.append({"topic": topic, "any_of": list(tools), "nudge": nudge})
    if given:
        fields = ", ".join(_SLOT_LABELS.get(g, g) for g in given)
        out.append({"topic": "datos_envio", "any_of": ["set_order_slot"], "fields": list(given),
                    "nudge": f"Guarda con set_order_slot lo que el cliente acaba de dar: {fields}."})
    return out


def _labels(items: Sequence[str]) -> str:
    names = [_SLOT_LABELS.get(i, i) for i in items]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def _next_step(stage: str | None, missing: list[str], given: list[str], result: Any, th: dict[str, float]) -> str:
    if stage == "etapa_descubrimiento":
        return "ayúdale a escoger un producto (catálogo o ficha)."
    if stage == "etapa_variantes":
        return f"pide solo lo que falta: {_labels(missing)}." if missing else "confirma la elección y sigue con los datos de envío."
    if stage == "etapa_datos_envio":
        save = f"guarda {_labels(given)} con set_order_slot y " if given else ""
        return f"{save}pide solo {_labels(missing)}." if missing else f"{save}muestra el resumen del pedido para que lo confirme."
    if stage == "etapa_cierre":
        if _p(result, "cierre.pide_cambio") >= th["given"]:
            return "haz el cambio que pide y vuelve a mostrar el resumen."
        if _p(result, "cierre.confirma_resumen") >= th["given"]:
            return "registra el pedido."
        return "muestra el resumen del pedido para que lo confirme."
    if stage == "etapa_postcierre":
        if _p(result, "postcierre.pregunta_pedido") >= th["given"]:
            return "consulta el estado con check_order_status antes de responder."
        if _p(result, "postcierre.comprobante") >= th["given"]:
            return "agradece y confirma que el equipo revisa el pago."
    return ""


def _guide(result: Any, context: Any, th: dict[str, float]) -> tuple[dict[str, Any], str | None]:
    stage = getattr(context, "stage", None)
    if not stage:
        return {}, None
    given = [slot for slot in SHIPPING_SLOTS if stage == "etapa_datos_envio" and _p(result, f"datos.{slot}") >= th["given"]]
    missing = [m for m in getattr(context, "missing", ()) if m not in given]
    going_back = stage in ("etapa_variantes", "etapa_datos_envio", "etapa_cierre") and _p(result, "etapa.cambia_producto") >= th["given"]
    stagnant = int(getattr(context, "stagnant", 0) or 0)
    step = _next_step(stage, missing, given, result, th)
    label = STAGE_LABELS.get(stage, stage)
    parts = [f"[ETAPA] {label[:1].upper() + label[1:]}."]
    if given:
        parts.append(f"El cliente acaba de dar: {_labels(given)}.")
    if missing:
        parts.append(f"Falta: {_labels(missing)}.")
    if going_back:
        parts.append("El cliente quiere ver otros productos: quita el anterior del pedido (quitar=true) y muéstrale opciones.")
    elif step:
        parts.append(f"Siguiente paso: {step}")
    if stagnant >= STAGNANT_TURNS:
        ask = f"pide de forma concreta {_labels(missing)}" if missing else "lleva al cliente al siguiente paso"
        parts.append(f"Llevan {stagnant} turnos en esta etapa sin un dato nuevo: {ask}.")
    guide = {"stage": stage, "given_now": given, "missing": missing, "going_back": going_back, "stagnant": stagnant,
             "next": step}
    return guide, " ".join(parts)


def decide_turn(
    result: Any,
    *,
    questionnaire: Any,
    context: Any = None,
    n_messages: int,
    thresholds: dict[str, float] | None = None,
) -> TurnOutcome:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    base = turno_v2.decide_turn(result, questionnaire=questionnaire, context=context, n_messages=n_messages, thresholds=th)
    if not base.plan.ok:
        return base
    guide, stage_note = _guide(result, context, th)
    required = _required([t.topic for t in base.plan.topics], result, getattr(context, "stage", None),
                         list(guide.get("given_now") or []), th)
    note_parts = [p for p in (base.note, stage_note) if p]
    if not base.note and stage_note:
        note_parts.insert(0, "[LECTURA DEL TURNO]")
    return TurnOutcome(
        plan=base.plan,
        topics=base.topics,
        note=" ".join(note_parts) if note_parts else None,
        coverage=base.coverage,
        reading=base.reading,
        tools={"required": required},
        guide=guide,
    )


__all__ = [
    "POLICY_ID",
    "complement_note",
    "coverage_decision",
    "coverage_rules",
    "decide_turn",
    "topic_rows",
]
