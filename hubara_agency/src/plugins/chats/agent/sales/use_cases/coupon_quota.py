"""Cupo por unidad de un cupón, visto por el bot de ventas.

Un cupón con filas de cupo (Marketing → Cupones) aplica SOLO a esas
combinaciones producto + color + aroma mientras queden unidades. Las
vendidas se derivan de los pedidos (SDK `quota_board`); si no se pueden leer,
el cupón con cupo NO se aplica (falla cerrada). Sin filas, el cupón se
comporta como siempre.

Los precios salen del catálogo y el descuento de `compute_discount` (L-19):
el LLM solo repite lo que dice el envelope.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.plugins.chats.agent.sales.use_cases.coupons import format_cop
from src.sdk.connectorkit import (
    REASON_QUOTA_EXHAUSTED,
    DiscountLineItem,
    PromotionDTO,
    PromotionsUnavailableError,
    QuotaStatus,
    compute_discount,
    quota_board,
    quota_exhausted,
)

REASON_QUOTA_UNAVAILABLE = "quota_unavailable"


@dataclass(frozen=True)
class QuotaOffer:
    """Lo que el bot puede decir del cupo de un cupón.

    `reason`: None (hay unidades) · "quota_exhausted" · "quota_unavailable".
    `units`: las combinaciones con unidades, con precio y precio con descuento
    (y `units_left` solo si el cupón permite decirlo, D3)."""

    has_quota: bool
    reason: str | None = None
    units: tuple[dict[str, Any], ...] = ()
    show_units_left: bool = True


_NO_QUOTA = QuotaOffer(has_quota=False)


def _cop_price(product: Any) -> int | None:
    for variant in getattr(product, "variants", None) or []:
        for price in getattr(variant, "prices", None) or []:
            if str(getattr(price, "currency_code", "")).lower() == "cop":
                try:
                    return int(round(float(price.amount)))
                except (TypeError, ValueError):
                    return None
    return None


async def _prices(catalog: Any) -> dict[str, int]:
    if catalog is None:
        return {}
    try:
        result = await catalog.search("", limit=500)
    except Exception:  # noqa: BLE001 — sin catálogo no hay precio (no se ofrece)
        return {}
    out: dict[str, int] = {}
    for product in getattr(result, "results", None) or []:
        price = _cop_price(product)
        if price is not None:
            out[str(getattr(product, "id", ""))] = price
    return out


def _unit(status: QuotaStatus, promotion: PromotionDTO, price: int, *, show: bool) -> dict[str, Any]:
    quota = status.quota
    per_unit = replace(promotion, min_subtotal_cop=None, product_ids=(), variant_ids=(),
                       collection_ids=(), tag_values=())
    line = DiscountLineItem(handle=quota.handle, quantity=1, unit_price_cop=price,
                            product_id=quota.product_id)
    discount = compute_discount(per_unit, [line]).discount_cop
    unit: dict[str, Any] = {"title": quota.title, "color": quota.color, "aroma": quota.aroma}
    if show:
        unit["units_left"] = status.units_left
    unit["price_cop"] = price
    unit["discounted_price_cop"] = max(price - discount, 0)
    return unit


async def quota_offer(promotion: PromotionDTO, *, quotas: Any, sales: Any, catalog: Any) -> QuotaOffer:
    """El cupo del cupón para el bot. Sin `quotas` (worker viejo) o sin filas:
    `has_quota=False` y el cupón aplica como siempre."""
    if quotas is None:
        return _NO_QUOTA
    sheet = quotas.get(promotion.id)
    if not sheet.quotas:
        return _NO_QUOTA
    try:
        board = await quota_board(sheet, sales)
    except PromotionsUnavailableError:
        return QuotaOffer(True, REASON_QUOTA_UNAVAILABLE, show_units_left=sheet.show_units_left)
    if quota_exhausted(board) == REASON_QUOTA_EXHAUSTED:
        return QuotaOffer(True, REASON_QUOTA_EXHAUSTED, show_units_left=sheet.show_units_left)
    prices = await _prices(catalog)
    units = tuple(
        _unit(s, promotion, prices[s.quota.product_id], show=sheet.show_units_left)
        for s in board
        if s.units_left > 0 and s.quota.product_id in prices
    )
    return QuotaOffer(True, None, units, sheet.show_units_left)


def unit_label(unit: dict[str, Any]) -> str:
    """"Cubo Love Rosado · Café"."""
    variant = " · ".join(v for v in (unit.get("color"), unit.get("aroma")) if v)
    return f"{unit['title']} {variant}".strip()


def units_text(units: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """"Cubo Love Rosado · Café ($21.000 → $18.900, quedan 3)"."""
    parts = []
    for unit in units:
        detail = f"{format_cop(unit['price_cop'])} → {format_cop(unit['discounted_price_cop'])}"
        if "units_left" in unit:
            detail += f", quedan {unit['units_left']}"
        parts.append(f"{unit_label(unit)} ({detail})")
    return ", ".join(parts)


def as_eligible(units: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Las unidades en la forma de `eligible_products` (la nota de cada turno
    recuerda ESTAS combinaciones, no el producto entero)."""
    return [
        {
            "handle": "",
            "title": unit_label(u),
            "price_cop": u["price_cop"],
            "discounted_price_cop": u["discounted_price_cop"],
        }
        for u in units
    ]


__all__ = [
    "REASON_QUOTA_UNAVAILABLE",
    "QuotaOffer",
    "as_eligible",
    "quota_offer",
    "unit_label",
    "units_text",
]
