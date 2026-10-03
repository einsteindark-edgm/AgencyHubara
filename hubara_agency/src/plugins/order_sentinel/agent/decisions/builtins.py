"""Los builtins del lector de Jev del Order Sentinel (PAQUETES_DE_DECISION.md F8).

Lo que el paquete `centinela` pide por nombre y sigue siendo código: el
texto que ve Jev (la conversación numerada y lo que el sistema ya sabe del
pedido) y qué mensajes pueden ser evidencia de un cambio (los nuevos; el
pago, solo del equipo: que el cliente diga que pagó no basta). Las
preguntas, la certeza que se pide y cómo se junta la evidencia son el
paquete. PURO: sin I/O.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_WHO = {"customer": "cliente", "human_operator": "equipo de la tienda", "bot": "bot de la tienda"}
_STAGE_LABEL = {
    "new": "nuevo",
    "preparing": "en preparación",
    "ready": "listo",
    "shipping": "en camino",
    "delivered": "entregado",
    "cancelled": "cancelado",
}
#: El cambio que solo prueba un mensaje del equipo.
PAYMENT = "pago"


def _messages(convo: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = convo.get("messages")
    return [m for m in messages if isinstance(m, Mapping)] if isinstance(messages, list) else []


def order_conversation(convo: Mapping[str, Any], *, max_chars: int) -> str:
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
        text = " ".join(str(message.get("text") or "").split())[:max_chars]
        media = "[adjuntó una imagen]" if message.get("has_media") else ""
        body = " ".join(part for part in (text, media) if part) or "(sin texto)"
        lines.append(f"[{i}] {_WHO.get(str(message.get('who')), 'otro')}: {body}")
    return "\n".join(lines)


def _is_new(message: Mapping[str, Any], since_ms: int | None) -> bool:
    at = message.get("at_ms")
    return since_ms is None or not isinstance(at, int) or at > since_ms


def evidence_candidates(
    convo: Mapping[str, Any], *, change: str | None, since_ms: int | None, limit: int, quote_chars: int
) -> list[dict[str, Any]]:
    """Los mensajes NUEVOS (desde `since_ms`, el último análisis; None =
    nunca) que podrían probar el cambio, los últimos `limit`: `number` (su
    número en la conversación), `text` (el texto exacto: la evidencia) y
    `quote` (en una línea, lo que lee Jev)."""
    allowed = ("human_operator",) if change == PAYMENT else ("human_operator", "customer")
    found = [
        (i, message["text"])
        for i, message in enumerate(_messages(convo), 1)
        if message.get("who") in allowed
        and isinstance(message.get("text"), str)
        and message["text"].strip()
        and _is_new(message, since_ms)
    ]
    return [{"number": i, "text": text, "quote": " ".join(text.split())[:quote_chars]} for i, text in found[-limit:]]


def no_reading(_convo: Mapping[str, Any]) -> None:
    return None


def no_evidence(_convo: Mapping[str, Any]) -> tuple[str, ...]:
    return ()


def jev(_convo: Mapping[str, Any], _rule: Any, value: Any) -> Any:
    return value


def same_value(a: Any, b: Any) -> bool:
    return a == b
