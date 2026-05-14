"""CheckoutVerificationPort — verificacion live contra la fuente de la verdad
(Medusa) en el momento del cierre de venta.

A diferencia del `CatalogPort` (snapshot de filesystem, que es la verdad
*durante* la conversacion), este port consulta la API en vivo para confirmar
precio y stock antes de que el cliente confirme el pedido. Cualquier
discrepancia entre snapshot y live debe ser surfaceada por el LLM al cliente
(decision A3 del plan: avisar y pedir confirmacion).

R-DIP: el Protocol vive aqui; los consumers (tools) reciben una instancia via
constructor injection. Las implementaciones viven en `medusa_checkout.py`
(default).

R-JSON: los DTOs son `@dataclass(frozen=True)` planos, JSON-safe — la tool
los serializa al envelope que el LLM lee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CheckoutItem:
    handle: str
    quantity: int


@dataclass(frozen=True)
class VerifiedItem:
    handle: str
    title: str
    snapshot_price: str | None
    live_price: str | None
    currency: str | None
    in_stock: bool
    discrepancy: bool  # True si snapshot_price != live_price


@dataclass(frozen=True)
class CheckoutVerification:
    """Resultado de verificar uno o mas items contra Medusa live.

    `catalog_available=False` indica que la API no respondio (timeout, 5xx,
    sin credenciales). En ese caso `verified` es False y `items` queda vacio
    — el LLM debe escalar (regla 13 del plan).
    """
    verified: bool
    catalog_available: bool
    items: list[VerifiedItem] = field(default_factory=list)
    error_detail: str | None = None


@runtime_checkable
class CheckoutVerificationPort(Protocol):
    async def verify_items(
        self, items: list[CheckoutItem]
    ) -> CheckoutVerification: ...
