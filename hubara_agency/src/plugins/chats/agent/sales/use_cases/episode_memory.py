"""Lo que el LLM sabe del episodio anterior cuando empieza uno nuevo — PURO.

Run 28a8e407 (2026-09-23): el historial del LLM se corta al abrir el episodio
(`llm_history_reset`, lo aplica el worker) y lo anterior viajaba como el
`motivo` que el LLM escribió al cerrar — que podía estar mal (la Trilogía con
un cupón que no era) — pegado por exoclaw delante de CADA mensaje del cliente.

Ahora es UNA línea armada con hechos del episodio cerrado (cómo cerró, qué
pedido, qué productos) que va al inicio del primer mensaje del episodio
nuevo: queda grabada una sola vez, justo donde empieza el historial limpio.
Tuteo colombiano (REGLA #1, guard test_no_voseo_in_agent_strings.py).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.shared.draft_items import draft_items

#: Cómo terminó el episodio, por tag de cierre (`episode_lifecycle`).
_OUTCOMES: dict[str, str] = {
    "COMPRA_EXITOSA": "terminó en una compra{order}",
    "CONFIRMADO_PAGO_PENDIENTE": (
        "terminó con un pedido registrado{order} que el equipo está "
        "gestionando (verificación del pago y envío)"
    ),
    "CONFIRMADO_SIN_DATOS": (
        "el cliente confirmó una compra{order} pero faltaron datos de envío; "
        "el equipo la está gestionando"
    ),
    "RECHAZO": "el cliente decidió no comprar",
    "TIMEOUT": "quedó sin respuesta del cliente por mucho tiempo",
    "CAMPAIGN_REPLY": "quedó abierta, sin compra",
}


def _item_label(item: dict[str, Any]) -> str:
    producto = str(item.get("producto") or "").strip()
    if not producto:
        return ""
    cantidad = str(item.get("cantidad") or "").strip()
    variants = [
        str(item[key]).strip()
        for key in ("aroma", "color", "diseno")
        if str(item.get(key) or "").strip()
    ]
    label = f"{cantidad}× {producto}" if cantidad else producto
    return f"{label} ({', '.join(variants)})" if variants else label


def previous_episode_summary(episode: dict[str, Any]) -> str:
    """Una línea determinista: cómo cerró el episodio y de qué se habló."""
    order_id = episode.get("order_id")
    order = f" ({order_id})" if isinstance(order_id, str) and order_id else ""
    template = _OUTCOMES.get(str(episode.get("closing_tag") or ""), "se cerró")
    summary = template.format(order=order)
    items = [
        label for label in map(_item_label, draft_items(episode.get("order_draft")))
        if label
    ]
    if items:
        summary += "; hablaron de " + ", ".join(items)
    coupon = episode.get("applied_coupon")
    code = coupon.get("code") if isinstance(coupon, dict) else None
    if isinstance(code, str) and code.strip():
        summary += f", con el cupón {code.strip()}"
    return summary


def with_previous_episode(episode: dict[str, Any], text: str) -> str:
    """El primer mensaje del episodio nuevo con lo anterior adelante."""
    return (
        "[Conversación anterior con este cliente, ya cerrada: "
        f"{previous_episode_summary(episode)}. Esta es una conversación nueva: "
        "no la retomes salvo que el cliente la mencione.]\n"
        f"{text}"
    )
