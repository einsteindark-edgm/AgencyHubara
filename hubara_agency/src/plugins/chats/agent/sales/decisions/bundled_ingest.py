"""Builtins de las lecturas del ingest (baja, retoma, compra, acuse, cupón) —
PAQUETES_DE_DECISION.md.

Viven aparte porque usan `messagingkit`, que también re-exporta una
activity de Temporal, y las reglas de `use_cases/`: el resolutor los carga
SOLO cuando una capacidad del paquete los pide (las del ingest). Así las
tools, que llegan al resolutor por `guards`, no arrastran Temporal (contrato
`tools-no-temporal`).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.plugins.chats.agent.sales.decisions.bundled import customer_text
from src.plugins.chats.agent.sales.decisions.context import customer_window, order_facts, real_wamid
from src.plugins.chats.agent.sales.use_cases.closing_ack import is_closing_ack
from src.plugins.chats.agent.sales.use_cases.coupons import coupon_in_play as _coupon_in_play
from src.plugins.chats.agent.sales.use_cases.coupons import coupon_talk_subject
from src.plugins.chats.shared.funnel import active_episode
from src.plugins.chats.shared.purchase_signals import classify_inbound_purchase_signal
from src.sdk.messagingkit import is_courtesy_text, is_opt_out_text, parse_reengagement_deferral

_HOUR_MS = 3_600_000


def _window(inp: Any) -> Any:
    return customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)


# ── reglas ──


def opt_out_text(inp: Any) -> bool:
    text = customer_text(inp)
    return bool(text) and is_opt_out_text(text)


def reengagement(inp: Any) -> dict[str, Any]:
    text = customer_text(inp)
    parsed = parse_reengagement_deferral(text, inp.now_ms, inp.tz) if text else None
    return {
        "deferral": {"kind": parsed.kind, "until_ms": parsed.until_ms} if parsed else None,
        "courtesy": bool(text) and is_courtesy_text(text),
    }


def purchase_signal(inp: Any) -> list[Any]:
    """`[tipo, fuente]` de `classify_inbound_purchase_signal`: el carrito y el
    botón «Confirmar» son estructurales; el texto lo lee la regla de hoy."""
    kind, source = classify_inbound_purchase_signal(
        customer_text(inp), interactive=getattr(inp, "interactive", None), order=getattr(inp, "order", None)
    )
    return [kind, source]


def closing_ack(inp: Any) -> bool:
    # La regla de hoy lee el texto tal como llega (una descripción de la
    # visión nunca calza con sus palabras de cortesía).
    return is_closing_ack(getattr(inp, "text", None))


def coupon_in_play(inp: Any) -> bool:
    return _coupon_in_play(inp.metadata, inp.text)


# ── vistas ──


def purchase_window(inp: Any) -> dict[str, Any]:
    """Si el cliente vio algo antes (`context`) y lo que se le preguntó cuando
    lo último fue una tarjeta (`asked_known`: confirmar_compra, …)."""
    window = _window(inp)
    return {"context": bool(window.lines), "asked_known": window.bot_asked_known}


def reply_gap(inp: Any) -> dict[str, Any]:
    """Si este mensaje abrió el episodio activo (`opens_episode`: el ingest lo
    abrió con él, también por la reentrada de un audio o una foto) y las horas
    enteras desde el último mensaje de la tienda (`hours_since_store`: el
    evento `assistant` más reciente del historial —el bot, el equipo o una
    plantilla— por su `timestamp`; None si nunca escribió o ninguno se puede
    fechar). No lee `last_inbound_at_ms`: el ingest ya lo pisó con este
    mensaje (incidente del 2026-10-06: «Buenas» 11 días después)."""
    metadata = getattr(inp, "metadata", None)
    episode = active_episode(dict(metadata)) if isinstance(metadata, Mapping) else None
    started = str((episode or {}).get("started_inbound_message_id") or "")
    message_id = str(getattr(inp, "message_id", None) or "")
    opens = bool(started) and bool(message_id) and real_wamid(started) == real_wamid(message_id)
    return {"opens_episode": opens, "hours_since_store": _hours_since_store(inp)}


def _event_ms(stamp: Any) -> int | None:
    """La hora de un evento del historial (ISO 8601; sin zona = UTC, como el
    laboratorio), o None si no se puede fechar."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        moment = datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None
    return int((moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def _hours_since_store(inp: Any) -> int | None:
    """Desde el final del historial (append-only): el último mensaje de la
    tienda que se puede fechar. Uno sin hora se salta."""
    for event in reversed(list(getattr(inp, "events", ()) or ())):
        if not isinstance(event, Mapping) or event.get("role") != "assistant":
            continue
        at_ms = _event_ms(event.get("timestamp"))
        if at_ms is not None:
            return max(0, int(getattr(inp, "now_ms", 0) or 0) - at_ms) // _HOUR_MS
    return None


# ── estado ──


def purchase_context(inp: Any) -> str | None:
    """Lo que el cliente vio antes, los hechos del pedido y ESTE MENSAJE. Solo
    cuando la regla lee el texto (el carrito y el botón no se preguntan)."""
    text = customer_text(inp)
    if text is None or purchase_signal(inp)[1] != "text":
        return None
    window = _window(inp)
    lines: list[str] = []
    if window.lines:
        lines += ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines]
    facts = order_facts(getattr(inp, "metadata", {}) or {}, stage=getattr(inp, "stage", "etapa_descubrimiento"))
    lines += ["HECHOS DEL PEDIDO", *facts, "ESTE MENSAJE DEL CLIENTE", f"[1] {text.strip()}"]
    return "\n".join(lines)


def message_after_close(inp: Any) -> str | None:
    """Lo que el cliente vio antes (la conversación ya cerrada) y su mensaje.
    Sin la despedida a la vista no se sabe a qué responde: no se pregunta."""
    text = customer_text(inp)
    if text is None:
        return None
    window = _window(inp)
    if not window.lines:
        return None
    # Encabezado neutro: el episodio pudo cerrarse por inactividad, sin despedida.
    return "\n".join([
        "CONTEXTO — lo que el cliente vio antes de este mensaje (esa conversación ya se había cerrado)",
        *window.lines,
        "MENSAJE DEL CLIENTE",
        f"[1] {text.strip()}",
    ])


def coupon_talk(inp: Any) -> str | None:
    """El cupón aplicado y sus productos, lo que el cliente vio antes y su
    mensaje. Sin cupón que dependa del texto, no se pregunta."""
    text = (inp.text or "").strip()
    subject = coupon_talk_subject(inp.metadata)
    if not text or subject is None:
        return None
    code, titles = subject
    lines = [f"CUPÓN APLICADO EN EL PEDIDO: {code}" + (f" — vale en: {', '.join(titles)}" if titles else "")]
    window = customer_window(list(inp.events), burst_wamids=set(), burst_size=0)
    if window.lines:
        lines += ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines]
    lines += ["ESTE MENSAJE DEL CLIENTE", f"[1] {text}"]
    return "\n".join(lines)
