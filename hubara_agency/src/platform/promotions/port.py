"""Port de promociones (cupones) — contrato + DTOs + dobles oficiales.

`PromotionDTO` es un SNAPSHOT JSON-safe de una promoción de Medusa v2: lo
suficiente para (a) validar un código, (b) calcular el descuento con los
precios del catálogo y (c) explicarle al cliente a qué aplica. Se persiste
tal cual en el episodio cuando el cliente aplica un cupón, así el cierre del
pedido recalcula el mismo descuento aunque Medusa cambie a mitad de chat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PromotionDTO:
    id: str
    code: str
    #: "percentage" | "fixed" | "buyget" (buyget no se calcula localmente)
    discount_type: str
    value: int
    currency_code: str | None
    #: "items" | "order" | "shipping_methods"
    target_type: str
    #: "across" | "each"
    allocation: str
    max_quantity: int | None
    #: Selección de productos (vacío en todos = todo el catálogo).
    product_ids: tuple[str, ...]
    variant_ids: tuple[str, ...]
    collection_ids: tuple[str, ...]
    #: Mínimo de compra (subtotal de productos) si la promo lo exige.
    min_subtotal_cop: int | None
    is_automatic: bool
    #: "active" | "inactive" | "draft"
    status: str
    starts_at_ms: int | None
    ends_at_ms: int | None
    #: Presupuesto de la campaña Medusa: "usage" (usos) | "spend" (monto).
    budget_type: str | None
    budget_limit: int | None
    budget_used: int | None
    #: Nombre de la campaña Medusa (texto para el cliente), si existe.
    description: str | None
    #: True = la promo tiene reglas (productos / mínimo de compra) que NO se
    #: pudieron leer (sin `values`, atributo desconocido). Falla CERRADA: el
    #: cupón no se aplica — jamás se asume "todo el catálogo".
    scope_unresolved: bool = False
    #: Condición por etiquetas de producto (`items.product.tags.id`), con el
    #: NOMBRE de cada etiqueta ("Color: Rosado"): el producto debe tener al
    #: menos una. Se suma (Y) a la selección de productos, como en Medusa.
    tag_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscountLineItem:
    """Línea del pedido con la identidad que las reglas de Medusa entienden."""

    handle: str
    quantity: int
    unit_price_cop: int
    product_id: str | None = None
    variant_id: str | None = None
    collection_id: str | None = None
    #: Etiquetas del producto ("Aroma: Café"), para las promos por etiqueta.
    tags: tuple[str, ...] = ()


class PromotionsUnavailableError(RuntimeError):
    """Medusa no respondió — el cupón no se puede validar ahora."""


@runtime_checkable
class PromotionsPort(Protocol):
    async def list_active(self) -> list[PromotionDTO]: ...

    async def get_by_code(self, code: str) -> PromotionDTO | None: ...


class NullPromotionsPort:
    """Sin Medusa configurado: no hay cupones."""

    async def list_active(self) -> list[PromotionDTO]:
        return []

    async def get_by_code(self, code: str) -> PromotionDTO | None:
        return None


class FakePromotionsPort:
    """Doble oficial para tests de plugins (P-27)."""

    def __init__(self, promotions: list[PromotionDTO] | None = None) -> None:
        self.promotions = list(promotions or [])
        self.calls: list[str] = []

    async def list_active(self) -> list[PromotionDTO]:
        self.calls.append("list_active")
        return [p for p in self.promotions if p.status == "active"]

    async def get_by_code(self, code: str) -> PromotionDTO | None:
        self.calls.append(f"get_by_code:{code}")
        wanted = code.strip().upper()
        return next((p for p in self.promotions if p.code.upper() == wanted), None)


__all__ = [
    "DiscountLineItem",
    "FakePromotionsPort",
    "NullPromotionsPort",
    "PromotionDTO",
    "PromotionsPort",
    "PromotionsUnavailableError",
]
