"""Lectura de Jev del estado del pedido (motor de decisiones, diseño v2 §07,
familia D; fase F8). Plan PURO: arma las preguntas y lee las respuestas; la
activity del snapshot hace la llamada.

Hoy el LLM del Order Sentinel (GraphAgents) lee la conversación y propone el
cambio. Con el lector `sombra` o `jev`, hubara le hace a Jev dos preguntas
cerradas antes de despachar el run:

  1. choice «¿Qué cambió?» sobre la conversación entera: nada, en
     preparación, listo, en camino, entregado o pago confirmado.
  2. Si cambió algo, un sí/no por cada mensaje NUEVO desde el último análisis
     que podría probarlo (del equipo o del cliente; el pago, solo del equipo):
     «¿Este mensaje dice que …?». La evidencia son esos mensajes, con su texto
     EXACTO. Lo viejo ya se analizó (y puede ser de un pedido anterior).

Las preguntas, sus opciones, lo que afirma cada cambio, la certeza que se
pide y cómo se junta la evidencia son el paquete del lector
(`order_sentinel/agent/decisions/bundles/centinela/`, PAQUETES_DE_DECISION.md
F8), certificado con `decisions check`: cambiarlas es otra versión del
paquete, no código.

El código arma el veredicto con la MISMA forma del LLM ({action, to_stage,
evidence, confidence}), así las guardas del grafo (etapa permitida, paso
adyacente, certeza alta, pago idempotente, una intención por pedido,
evidencia textual) quedan iguales para las dos fuentes. Si Jev duda, cae o
tarda, no hay veredicto de Jev: decide el LLM, como hoy.

El lector lo fija Terraform (`ORDER_SENTINEL_READER`: off | shadow | on) y
nace apagado: `reglas` = hoy, sin llamar a Jev.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.plugins.order_sentinel.agent import decisions
from src.sdk.connectorkit import TypedQuestion
from src.sdk.decisionkit import DOUBT, answers_from_result

#: El nombre de la capacidad en la cola de desacuerdos.
CAPABILITY = "estado_pedido"
#: Perfil del oráculo (`src/platform/perception/profiles.yaml`).
ORACLE_PROFILE = "jev-1.13"
#: La pregunta «¿qué cambió?» del paquete (la leen las pruebas y la traza).
CHANGE_QID = "estado_pedido.cambio"

#: Terraform (off | shadow | on) → el lector (reglas | sombra | jev).
_READER_OF = {"off": "reglas", "shadow": "sombra", "on": "jev"}
#: El cambio leído → la etapa a la que pasa el pedido (la forma del LLM).
_STAGE_OF = {"preparacion": "preparing", "listo": "ready", "en_camino": "shipping", "entregado": "delivered"}

#: Las casillas personales del borrador que se tapan antes de que salga el texto.
_PERSONAL_SLOTS = ("nombre_recibe", "direccion", "barrio", "telefono", "cedula")
_NAME_SLOTS = ("nombre_recibe",)
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")
_VALID_ORDER_PREFIXES = ("order_", "draft_")


def reader_mode(raw: str | None) -> str:
    """El valor de Terraform → el lector. Cualquier otro valor = `reglas`."""
    return _READER_OF.get((raw or "").strip().lower(), "reglas")


def _capability(name: str) -> Any:
    return decisions.active_bundle().capability(name)


def conversation_state(convo: Mapping[str, Any]) -> str:
    """Lo que ve Jev: lo que el sistema ya sabe del pedido y la conversación
    numerada (el mismo texto va a la cola de desacuerdos, anonimizado)."""
    table = _capability("cambio")
    return decisions.call(table.spec.state, convo)


def _typed(questions: Sequence[Any]) -> list[TypedQuestion]:
    return [TypedQuestion(id=q.id, kind=q.kind, text=q.text, criteria=dict(q.criteria)) for q in questions]


def change_request(convo: Mapping[str, Any]) -> tuple[str, list[TypedQuestion]]:
    """La primera pregunta: qué cambió en el pedido que el sistema no sabe."""
    table = _capability("cambio")
    return decisions.call(table.spec.state, convo), _typed(table.questions_for({}))


def _candidates(convo: Mapping[str, Any], change: str | None, since_ms: int | None) -> list[dict[str, Any]]:
    """Los mensajes nuevos que podrían probar el cambio (`since_ms` = hasta
    dónde se analizó antes; None = nunca)."""
    table = _capability("evidencia")
    return decisions.call(table.spec.items, convo, change=change, since_ms=since_ms)


def evidence_request(
    convo: Mapping[str, Any], change: str, *, since_ms: int | None = None
) -> tuple[str, list[TypedQuestion]] | None:
    """La segunda pregunta: un sí/no por cada mensaje candidato. None si no
    hay ninguno, o si el cambio no se prueba con un mensaje (`nada`)."""
    table = _capability("evidencia")
    questions = table.each_questions(_candidates(convo, change, since_ms), {"change": change})
    if not questions:
        return None
    return decisions.call(table.spec.state, convo), _typed(questions)


def _choice_p(answer: Any) -> float | None:
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(getattr(answer, "choice", None), getattr(answer, "confidence", None))
    return float(p) if isinstance(p, (int, float)) else None


def _decided_change(result: Any) -> str | None:
    """El cambio que la tabla `cambio` del paquete decide con las respuestas
    de Jev; None si duda (o si Jev cayó)."""
    if not getattr(result, "ok", False):
        return None
    table = _capability("cambio")
    value = table.decide(answers=answers_from_result(table.spec.questions, result))
    return None if value is DOUBT else value


def _evidence(convo: Mapping[str, Any], change: str, second: Any, since_ms: int | None) -> list[str]:
    """Los mensajes que la tabla `evidencia` del paquete da por prueba (su
    texto exacto, en orden)."""
    table = _capability("evidencia")
    items = _candidates(convo, change, since_ms)
    questions = table.each_questions(items, {"change": change}, all_items=True)
    answers = answers_from_result(questions, second) if second is not None else {}
    value = table.decide(answers=answers, inp={"change": change}, items=items)
    return [] if value is DOUBT else list(value)


def _compact(result: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for answer in getattr(result, "answers", ()) or ():
        if answer.kind == "choice":
            p = _choice_p(answer)
            out.append({"id": answer.id, "choice": answer.choice, "p": round(p, 3) if p is not None else None})
        else:
            out.append({"id": answer.id, "p": round(answer.p, 3) if isinstance(answer.p, (int, float)) else None})
    return out


def reading_from(
    convo: Mapping[str, Any], first: Any, second: Any = None, *, since_ms: int | None = None
) -> dict[str, Any]:
    """Las respuestas de Jev → la lectura que viaja en el snapshot:
    `{verdict, model, error, answers}`. `verdict` = None cuando Jev dudó,
    cayó o tardó (decide el LLM); `error` = el motivo si cayó. `since_ms`, el
    mismo que armó la pregunta de evidencia."""
    reading: dict[str, Any] = {
        "verdict": None,
        "model": getattr(first, "model", "") or getattr(second, "model", "") or "",
        "error": None,
        "answers": _compact(first) + _compact(second),
    }
    if not getattr(first, "ok", False):
        reading["error"] = getattr(first, "error", None) or "provider_error"
        return reading
    change = _decided_change(first)
    if change is None:
        return reading
    if change == "nada":
        reading["verdict"] = {"action": "none", "evidence": [], "confidence": "high"}
        return reading
    if second is not None and not getattr(second, "ok", False):
        reading["error"] = getattr(second, "error", None) or "provider_error"
        return reading
    evidence = _evidence(convo, change, second, since_ms)
    if not evidence:
        return reading
    if change == "pago":
        reading["verdict"] = {"action": "confirm_payment", "evidence": evidence, "confidence": "high"}
    else:
        reading["verdict"] = {
            "action": "transition",
            "to_stage": _STAGE_OF[change],
            "evidence": evidence,
            "confidence": "high",
        }
    return reading


def needs_evidence(first: Any) -> str | None:
    """El cambio que Jev leyó con certeza y que pide la segunda pregunta."""
    change = _decided_change(first)
    return change if change is not None and change != "nada" else None


def redact_terms(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Lo que hay que tapar de ESTE cliente antes de que el texto salga hacia
    Jev: las casillas personales del borrador del episodio que tiene el pedido
    (el adaptador igual tapa lo genérico: teléfonos, correos, direcciones)."""
    episodes = metadata.get("episodes")
    if not isinstance(episodes, Sequence) or isinstance(episodes, str):
        return ()
    for episode in reversed(episodes):
        if not isinstance(episode, Mapping):
            continue
        order_id = episode.get("order_id")
        if not (isinstance(order_id, str) and order_id.startswith(_VALID_ORDER_PREFIXES)):
            continue
        draft = episode.get("order_draft")
        slots = draft.get("slots") if isinstance(draft, Mapping) else None
        if not isinstance(slots, Mapping):
            return ()
        terms: set[str] = set()
        for key in _PERSONAL_SLOTS:
            value = slots.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            terms.add(value.strip())
            if key in _NAME_SLOTS:
                terms.update(_NAME_TOKEN_RE.findall(value))
        return tuple(sorted(terms))
    return ()


__all__ = [
    "CAPABILITY",
    "CHANGE_QID",
    "ORACLE_PROFILE",
    "change_request",
    "conversation_state",
    "evidence_request",
    "needs_evidence",
    "reader_mode",
    "reading_from",
    "redact_terms",
]
