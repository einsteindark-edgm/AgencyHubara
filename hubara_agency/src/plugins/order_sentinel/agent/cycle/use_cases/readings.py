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

from src.sdk.connectorkit import TypedQuestion

#: El nombre de la capacidad en la cola de desacuerdos.
CAPABILITY = "estado_pedido"
#: Perfil del oráculo (`src/platform/perception/profiles.yaml`).
ORACLE_PROFILE = "jev-1.13"
CHANGE_QID = "estado_pedido.cambio"
EVIDENCE_QID = "estado_pedido.evidencia.{}"
#: Certeza que pide el código para el cambio y para cada evidencia.
THRESHOLD = 0.85
#: Cuántos mensajes candidatos se preguntan como evidencia (los últimos). En
#: el único despacho real de 30 días la prueba era el candidato 12 contando
#: desde el final de la ventana; entre los mensajes nuevos, el 12 de 13.
MAX_EVIDENCE = 16
#: Tope de cada mensaje en lo que ve Jev.
MAX_CHARS = 500

#: Terraform (off | shadow | on) → el lector (reglas | sombra | jev).
_READER_OF = {"off": "reglas", "shadow": "sombra", "on": "jev"}

_WHO = {"customer": "cliente", "human_operator": "equipo de la tienda", "bot": "bot de la tienda"}
_STAGE_LABEL = {
    "new": "nuevo",
    "preparing": "en preparación",
    "ready": "listo",
    "shipping": "en camino",
    "delivered": "entregado",
    "cancelled": "cancelado",
}

_CHANGES: Mapping[str, str] = {
    "nada": (
        "Nada nuevo: no hay una señal clara, solo hay anuncios a futuro («mañana te lo envío»), dudas o "
        "preguntas, o el sistema ya lo sabe."
    ),
    "preparacion": "El equipo de la tienda dice que ya está preparando o haciendo el pedido.",
    "listo": "El equipo de la tienda dice que el pedido ya está listo.",
    "en_camino": "El pedido ya salió: el equipo dice que ya lo envió, que ya lo despachó o que va en camino.",
    "entregado": "El pedido ya se entregó: el cliente dice que ya le llegó o el equipo confirma la entrega.",
    "pago": "El equipo de la tienda confirma que recibió o verificó el pago (que el cliente diga que pagó no basta).",
}
_CLAIM = {
    "preparacion": "el pedido ya se está preparando",
    "listo": "el pedido ya está listo",
    "en_camino": "el pedido ya salió o va en camino",
    "entregado": "el pedido ya se entregó",
    "pago": "la tienda ya recibió o verificó el pago",
}
_STAGE_OF = {"preparacion": "preparing", "listo": "ready", "en_camino": "shipping", "entregado": "delivered"}
_YES_NO = {"true": "sí", "false": "no"}

#: Las casillas personales del borrador que se tapan antes de que salga el texto.
_PERSONAL_SLOTS = ("nombre_recibe", "direccion", "barrio", "telefono", "cedula")
_NAME_SLOTS = ("nombre_recibe",)
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")
_VALID_ORDER_PREFIXES = ("order_", "draft_")


def reader_mode(raw: str | None) -> str:
    """El valor de Terraform → el lector. Cualquier otro valor = `reglas`."""
    return _READER_OF.get((raw or "").strip().lower(), "reglas")


def _messages(convo: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = convo.get("messages")
    return [m for m in messages if isinstance(m, Mapping)] if isinstance(messages, list) else []


def conversation_state(convo: Mapping[str, Any]) -> str:
    """Lo que ve Jev: lo que el sistema ya sabe del pedido y la conversación
    numerada (el mismo texto va a la cola de desacuerdos, anonimizado)."""
    stage = _STAGE_LABEL.get(str(convo.get("current_stage")), str(convo.get("current_stage") or "desconocido"))
    paid = "sí" if convo.get("payment_confirmed") else "no"
    lines = [
        "Conversación de WhatsApp entre una tienda y un cliente sobre un pedido (en orden; [n] es el número "
        "del mensaje).",
        f"Lo que el sistema de la tienda ya sabe: pedido {stage}; pago confirmado: {paid}.",
    ]
    for i, message in enumerate(_messages(convo), 1):
        text = " ".join(str(message.get("text") or "").split())[:MAX_CHARS]
        media = "[adjuntó una imagen]" if message.get("has_media") else ""
        body = " ".join(part for part in (text, media) if part) or "(sin texto)"
        lines.append(f"[{i}] {_WHO.get(str(message.get('who')), 'otro')}: {body}")
    return "\n".join(lines)


def change_request(convo: Mapping[str, Any]) -> tuple[str, list[TypedQuestion]]:
    """La primera pregunta: qué cambió en el pedido que el sistema no sabe."""
    return conversation_state(convo), [
        TypedQuestion(
            id=CHANGE_QID,
            kind="choice",
            text="Según la conversación, ¿qué cambió en el pedido que el sistema de la tienda todavía no sabe?",
            criteria=dict(_CHANGES),
        )
    ]


def _is_new(message: Mapping[str, Any], since_ms: int | None) -> bool:
    at = message.get("at_ms")
    return since_ms is None or not isinstance(at, int) or at > since_ms


def _candidates(convo: Mapping[str, Any], change: str, since_ms: int | None) -> list[tuple[int, str]]:
    """(número, texto exacto) de los mensajes nuevos que podrían probar el
    cambio. `since_ms` = hasta dónde se analizó antes (None = nunca)."""
    allowed = ("human_operator",) if change == "pago" else ("human_operator", "customer")
    out = [
        (i, message["text"])
        for i, message in enumerate(_messages(convo), 1)
        if message.get("who") in allowed
        and isinstance(message.get("text"), str)
        and message["text"].strip()
        and _is_new(message, since_ms)
    ]
    return out[-MAX_EVIDENCE:]


def evidence_request(
    convo: Mapping[str, Any], change: str, *, since_ms: int | None = None
) -> tuple[str, list[TypedQuestion]] | None:
    """La segunda pregunta: un sí/no por cada mensaje candidato. None si no
    hay ninguno (entonces nada lo prueba)."""
    claim = _CLAIM.get(change)
    candidates = _candidates(convo, change, since_ms)
    if claim is None or not candidates:
        return None
    return conversation_state(convo), [
        TypedQuestion(
            id=EVIDENCE_QID.format(i),
            kind="noul",
            text=(
                f"¿El mensaje [{i}] («{' '.join(text.split())[:200]}») dice que {claim}? Un anuncio a futuro, "
                "una duda o una pregunta no cuenta."
            ),
            criteria=_YES_NO,
        )
        for i, text in candidates
    ]


def _choice_p(answer: Any) -> float | None:
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(getattr(answer, "choice", None), getattr(answer, "confidence", None))
    return float(p) if isinstance(p, (int, float)) else None


def _decided_change(result: Any) -> str | None:
    answer = result.answer(CHANGE_QID) if getattr(result, "ok", False) else None
    if answer is None or answer.choice not in _CHANGES:
        return None
    p = _choice_p(answer)
    return answer.choice if p is not None and p >= THRESHOLD else None


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
    evidence = []
    for i, text in _candidates(convo, change, since_ms):
        answer = second.answer(EVIDENCE_QID.format(i)) if second is not None else None
        p = getattr(answer, "p", None)
        if isinstance(p, (int, float)) and p >= THRESHOLD:
            evidence.append(text)
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
