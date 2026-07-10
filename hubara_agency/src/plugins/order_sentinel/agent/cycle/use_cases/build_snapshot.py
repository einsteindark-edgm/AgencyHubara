"""Plan PURO del snapshot de order-sentinel (sin I/O ni reloj — molde
reengagement/build_snapshot).

Filtra las sesiones del vault a las que el agente debe mirar: tag HUMANO +
orden vinculada + mensajes NUEVOS desde el watermark. El historial ya viene
parseado (líneas del JSONL de FilesystemMessageHistoryStore); acá solo se
mapea al contrato del agente (who/text/at_ms/has_media) y se recorta la
ventana (nuevos + hasta 10 previos de contexto — el LLM interpreta la
conversación completa, no un mensaje suelto).

`current_stage` / `payment_confirmed` NO se ponen acá: los inyecta la
activity consultando la API de orders (el use case no conoce Medusa).

El snapshot lleva TOP-LEVEL `watermarks: {session_id: max_at_ms_de_los_
NUEVOS}` — "hasta dónde analicé" — que el workflow acarrea a
`execute_order_intents` al cierre del ciclo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

#: prefijos de order_id que cuentan como orden REAL de Medusa (los stubs
#: HUB-/AUDIT- del vault no son órdenes transicionables).
_VALID_ORDER_PREFIXES = ("order_", "draft_")

#: cuántos mensajes PREVIOS al watermark viajan como contexto.
_CONTEXT_MESSAGES = 10


def _linked_order_id(metadata: dict[str, Any]) -> str | None:
    """El order_id vinculado: el ÚLTIMO episodio que tenga uno (un episodio
    final sin orden no borra el vínculo); fallback registered_order legacy."""
    for episode in reversed(metadata.get("episodes") or []):
        order_id = episode.get("order_id")
        if isinstance(order_id, str) and order_id.startswith(_VALID_ORDER_PREFIXES):
            return order_id
    legacy = (metadata.get("registered_order") or {}).get("order_id")
    if isinstance(legacy, str) and legacy.startswith(_VALID_ORDER_PREFIXES):
        return legacy
    return None


def _at_ms(event: dict[str, Any]) -> int | None:
    """timestamp ISO UTC (como lo persiste FilesystemMessageHistoryStore) → epoch ms."""
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _who(event: dict[str, Any]) -> str:
    if event.get("role") == "user":
        return "customer"
    # role=assistant: el discriminador bot/operador es `sender` (el historial
    # persiste al humano como assistant+sender=human para no romper la API
    # del LLM al retomar).
    return "human_operator" if event.get("sender") == "human" else "bot"


def _message(event: dict[str, Any], at_ms: int) -> dict[str, Any]:
    content = event.get("content")
    return {
        "who": _who(event),
        # content multimodal (lista) → texto vacío; la señal queda en has_media.
        "text": content if isinstance(content, str) else "",
        "at_ms": at_ms,
        "has_media": bool(event.get("image_url")),
    }


def conversation_entry(
    session_id: str,
    metadata: dict[str, Any],
    history_events: list[dict[str, Any]],
    watermark_ms: int | None,
) -> tuple[dict[str, Any], int] | None:
    """Una sesión → (entry del snapshot, nuevo watermark). None si la sesión
    no aplica (sin tag HUMANO / sin orden / sin mensajes nuevos)."""
    if metadata.get("tag") != "HUMANO":
        return None
    order_id = _linked_order_id(metadata)
    if order_id is None:
        return None

    timed = sorted(
        (
            (at, e)
            for e in history_events
            if (at := _at_ms(e)) is not None
        ),
        key=lambda pair: pair[0],
    )
    new = [(at, e) for at, e in timed if watermark_ms is None or at > watermark_ms]
    if not new:
        return None
    context = [(at, e) for at, e in timed if watermark_ms is not None and at <= watermark_ms]
    window = context[-_CONTEXT_MESSAGES:] + new

    entry = {
        "session_id": session_id,
        "order_id": order_id,
        "messages": [_message(e, at) for at, e in window],
    }
    return entry, max(at for at, _ in new)


def build_snapshot_from_sessions(
    now_ms: int,
    sessions: list[tuple[str, dict[str, Any], list[dict[str, Any]], int | None]],
) -> dict[str, Any]:
    conversations: list[dict[str, Any]] = []
    watermarks: dict[str, int] = {}
    for session_id, metadata, history_events, watermark_ms in sessions:
        result = conversation_entry(session_id, metadata, history_events, watermark_ms)
        if result is None:
            continue
        entry, new_watermark = result
        conversations.append(entry)
        watermarks[session_id] = new_watermark
    return {
        "schema_version": 1,
        "now_ms": now_ms,
        "conversations": conversations,
        "watermarks": watermarks,
    }
