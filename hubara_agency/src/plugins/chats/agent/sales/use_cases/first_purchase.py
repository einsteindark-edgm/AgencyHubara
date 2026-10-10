"""¿El cliente ya compró antes? — para los cupones de primera compra.

Caso del 2026-10-09: la página ofrece un 5 % «para nuevos clientes en su primer
pedido». Medusa no lo puede saber por sí sola (los pedidos del bot son
borradores y no cuentan usos), así que se mira a la PERSONA:

  * todos los pedidos de su número en Medusa (`customer_orders`): los de
    cualquier conversación y los cargados para un cliente con su teléfono;
  * los que registró esta conversación, leídos en OrderFacts (regla: el vault
    guarda el vínculo, nunca el estado del pedido).

El pedido del episodio activo no cuenta: es justamente la primera compra.
Cuenta un pedido que siguió adelante, pagado o no; uno cancelado, de prueba o
borrado en Medusa no cuenta. Si Medusa no confirma lo que podría contar, no se
adivina (`FirstPurchaseUnknown`: el cupón no se aplica).

Lo que no se ve: compras de otro número de WhatsApp, un pedido de otro cliente
que solo trae el teléfono en la dirección de envío y lo vendido antes del bot.
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


def _counts(fact: Any) -> bool:
    """Un pedido que siguió adelante: ni cancelado ni de prueba."""
    return fact.stage != "cancelled" and not fact.is_test


async def bought_before(
    metadata: dict[str, Any],
    order_facts: Any = None,
    *,
    customer_orders: Any = None,
    session_key: str | None = None,
) -> bool:
    """True si un pedido anterior de la persona cuenta como compra.

    `customer_orders` + `session_key`: los pedidos de su número en Medusa
    (`CustomerOrdersPort`). Levanta `FirstPurchaseUnknown` si Medusa no
    confirmó un pedido que podría contar. Sin `order_facts` (no hay cómo
    mirar), un pedido registrado en la conversación cuenta."""
    current = (get_active_episode(metadata) or {}).get("order_id")
    if customer_orders is not None and session_key:
        try:
            orders = await asyncio.wait_for(customer_orders.orders_of(session_key), timeout=ORDER_FACTS_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: no se adivina
            raise FirstPurchaseUnknown(f"los pedidos de la persona no respondieron: {type(exc).__name__}") from exc
        if any(_counts(fact) for fact in orders if fact.order_id != current):
            return True
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
        if _counts(fact):
            return True
    if unknown:
        raise FirstPurchaseUnknown("un pedido anterior no se pudo confirmar en Medusa")
    return False


__all__ = ["FirstPurchaseUnknown", "ORDER_FACTS_TIMEOUT_S", "bought_before", "previous_order_ids"]
