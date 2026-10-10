"""¿El cliente ya compró antes? — para los cupones de primera compra.

Caso del 2026-10-09: la página ofrece un 5 % de bienvenida en la primera
compra. Medusa no lo puede saber (los pedidos del bot son borradores y no
cuentan usos), así que lo decide la conversación: los pedidos que registró
ANTES del episodio activo (el del episodio en curso es justamente la primera
compra), leídos en OrderFacts (regla: el vault guarda el vínculo, nunca el
estado del pedido). Cuenta un pedido que siguió adelante, pagado o no; uno
cancelado, de prueba o borrado en Medusa no cuenta.

Lo que no se ve: compras de otro número de WhatsApp, pedidos cargados a mano
en Medusa Admin y lo vendido antes del bot.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode

#: Lo que se espera a OrderFacts antes de decir que no se sabe.
ORDER_FACTS_TIMEOUT_S = 3.0


class FirstPurchaseUnknown(RuntimeError):
    """No se pudo confirmar si un pedido anterior cuenta (Medusa no respondió)."""


def _registered_ids(metadata: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for entry in metadata.get("registered_orders_history") or []:
        if isinstance(entry, dict) and entry.get("success") and isinstance(entry.get("order_id"), str):
            ids.append(entry["order_id"])
    registered = metadata.get("registered_order")
    if isinstance(registered, dict) and registered.get("success") and isinstance(registered.get("order_id"), str):
        ids.append(registered["order_id"])
    for episode in metadata.get("episodes") or []:
        if isinstance(episode, dict) and isinstance(episode.get("order_id"), str):
            ids.append(episode["order_id"])
    return ids


def previous_order_ids(metadata: dict[str, Any]) -> tuple[str, ...]:
    """Los pedidos que esta conversación registró antes del episodio activo."""
    active = get_active_episode(metadata) or {}
    current = active.get("order_id")
    out: list[str] = []
    for order_id in _registered_ids(metadata):
        if order_id and order_id != current and order_id not in out:
            out.append(order_id)
    return tuple(out)


async def bought_before(metadata: dict[str, Any], order_facts: Any = None) -> bool:
    """True si un pedido anterior cuenta como compra. Levanta
    `FirstPurchaseUnknown` si Medusa no confirmó un pedido que podría contar.
    Sin `order_facts` (no hay cómo mirar), un pedido registrado cuenta."""
    ids = previous_order_ids(metadata)
    if not ids:
        return False
    if order_facts is None:
        return True
    try:
        snapshot = await asyncio.wait_for(order_facts.get_facts(ids), timeout=ORDER_FACTS_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: no se adivina
        raise FirstPurchaseUnknown(f"OrderFacts no respondió: {type(exc).__name__}") from exc
    unknown = False
    for order_id in ids:
        fact = snapshot.facts.get(order_id)
        if fact is None:
            unknown = unknown or order_id in snapshot.unresolved
            continue
        if fact.stage != "cancelled" and not fact.is_test:
            return True
    if unknown:
        raise FirstPurchaseUnknown("un pedido anterior no se pudo confirmar en Medusa")
    return False


__all__ = ["FirstPurchaseUnknown", "ORDER_FACTS_TIMEOUT_S", "bought_before", "previous_order_ids"]
