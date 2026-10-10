"""Los pedidos de UNA persona en Medusa — para el cupón de bienvenida.

Caso del 2026-10-09: la página ofrece un 5 % «para nuevos clientes en su primer
pedido». Lo que registró la conversación no alcanza para saber si la persona
ya compró: un pedido de otra conversación con el mismo número, o el que el
operador cargó para ese cliente, también cuenta.

La persona es su número de WhatsApp. En Medusa son:

  * el cliente a nombre de la sesión — el bot y «Crear pedido» registran a
    nombre de `wa+<sesión>@hubara.local` (`medusa_order._synthesize_email`);
  * todo cliente con ese teléfono: `q` busca el texto dentro del correo, el
    nombre y el teléfono, así que se confirma que el teléfono TERMINA en los
    10 dígitos del número (un pedazo en medio de otro número no cuenta).

Sus pedidos y borradores (`customer_id[]`) salen con el MISMO mapeo de la vista
Órdenes (`MedusaOrderQuery.summary_of` → `OrderFacts`): etapa, pago y «de
prueba» se leen igual que en el dashboard (gotcha 13).

Lo que no se ve: un pedido de otro cliente que solo trae el teléfono en la
dirección de envío, y más de `limit` pedidos o borradores de la persona.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from src.platform.orders.facts import OrderFacts
from src.platform.orders.medusa_order import _synthesize_email

#: Lo que se lee de cada cliente: lo justo para confirmar que es la persona.
_CUSTOMER_FIELDS = "id,email,phone"


class CustomerOrdersUnavailableError(RuntimeError):
    """Medusa no respondió: no se sabe qué pedidos tiene la persona."""


@runtime_checkable
class CustomerOrdersPort(Protocol):
    """Contrato: los pedidos (y borradores) de la persona de una sesión."""

    async def orders_of(self, session_key: str) -> tuple[OrderFacts, ...]:
        """Levanta `CustomerOrdersUnavailableError` si Medusa no respondió:
        el que llama decide (un cupón de primera compra no se adivina)."""
        ...


def _digits(raw: Any) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def session_phone_digits(session_key: str) -> str | None:
    """Los 10 dígitos del número de la sesión (`wa_57…` → `3…`), o None."""
    digits = _digits(session_key)
    return digits[-10:] if len(digits) >= 10 else None


class MedusaCustomerOrders:
    """Adapter real: clientes de la persona → sus pedidos y borradores."""

    def __init__(self, client: Any, query: Any, *, limit: int = 50) -> None:
        """`query`: el `MedusaOrderQuery` cuyo mapeo usa la vista Órdenes."""
        self._client = client
        self._query = query
        self._limit = limit

    async def orders_of(self, session_key: str) -> tuple[OrderFacts, ...]:
        try:
            ids = await self._customer_ids(session_key)
            if not ids:
                return ()
            fields = self._client.DEFAULT_ORDER_LIST_FIELDS
            orders, drafts = await asyncio.gather(
                self._client.list_orders(limit=self._limit, customer_id=ids, fields=fields),
                self._client.list_draft_orders(limit=self._limit, customer_id=ids, fields=fields),
            )
        except Exception as exc:  # noqa: BLE001 — el vendor no cruza el port
            raise CustomerOrdersUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        facts = [
            OrderFacts.from_summary(self._query.summary_of(raw, is_draft=False))
            for raw in orders.get("orders") or []
            if isinstance(raw, dict) and raw.get("id")
        ]
        facts += [
            OrderFacts.from_summary(self._query.summary_of(raw, is_draft=True))
            for raw in drafts.get("draft_orders") or []
            if isinstance(raw, dict) and raw.get("id")
        ]
        return tuple(facts)

    async def _customer_ids(self, session_key: str) -> list[str]:
        email = _synthesize_email(session_key)
        phone = session_phone_digits(session_key)
        lookups = [self._client.list_customers(email=email, limit=5, fields=_CUSTOMER_FIELDS)]
        if phone:
            lookups.append(self._client.list_customers(q=phone, limit=20, fields=_CUSTOMER_FIELDS))
        ids: list[str] = []
        for page in await asyncio.gather(*lookups):
            for row in page.get("customers") or []:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                same_session = str(row.get("email") or "").lower() == email.lower()
                same_phone = phone is not None and _digits(row.get("phone")).endswith(phone)
                if (same_session or same_phone) and str(row["id"]) not in ids:
                    ids.append(str(row["id"]))
        return ids


class NoCustomerOrders:
    """Sin Medusa configurado: ahí nadie tiene pedidos (decide la conversación)."""

    async def orders_of(self, session_key: str) -> tuple[OrderFacts, ...]:
        return ()


class InMemoryCustomerOrders:
    """Fake oficial: pedidos por sesión, o Medusa caído (`available=False`)."""

    def __init__(
        self, orders: Mapping[str, Iterable[OrderFacts]] | None = None, *, available: bool = True
    ) -> None:
        self._orders = {key: tuple(value) for key, value in (orders or {}).items()}
        self.available = available
        self.calls: list[str] = []

    async def orders_of(self, session_key: str) -> tuple[OrderFacts, ...]:
        self.calls.append(session_key)
        if not self.available:
            raise CustomerOrdersUnavailableError("Medusa no respondió (fake)")
        return self._orders.get(session_key, ())


__all__ = [
    "CustomerOrdersPort",
    "CustomerOrdersUnavailableError",
    "InMemoryCustomerOrders",
    "MedusaCustomerOrders",
    "NoCustomerOrders",
    "session_phone_digits",
]
