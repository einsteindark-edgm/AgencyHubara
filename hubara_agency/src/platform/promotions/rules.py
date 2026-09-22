"""Reglas PURAS de cupones: validar el código y calcular el descuento.

Sin I/O. El monto nace acá, del snapshot de la promoción + los precios del
catálogo (L-19: un descuento es un monto y no lo decide el LLM).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.platform.promotions.port import DiscountLineItem, PromotionDTO

#: Forma permitida de un cupón. SIN `_`: `VELAS_10` colisiona con el patrón
#: de tag interno del guard de egreso (`[A-Z]{2,}(_[A-Z0-9]{2,})+`) y el bot
#: enmudecería al escribirlo (memoria coupon-tag-shape-collision).
COUPON_CODE_RE = re.compile(r"[A-Z0-9]{3,20}")

REASON_INVALID_FORMAT = "invalid_format"
REASON_NOT_FOUND = "not_found"
REASON_INACTIVE = "inactive"
REASON_NOT_STARTED = "not_started"
REASON_EXPIRED = "expired"
REASON_BUDGET = "budget_exhausted"


@dataclass(frozen=True)
class CouponResolution:
    ok: bool
    reason: str | None
    promotion: PromotionDTO | None


def normalize_coupon_code(raw: str | None) -> str:
    return (raw or "").strip().upper()


def resolve_coupon(
    raw_code: str | None, promotions: list[PromotionDTO], *, now_ms: int
) -> CouponResolution:
    """Busca el código entre las promociones y valida vigencia/presupuesto."""
    code = normalize_coupon_code(raw_code)
    if not COUPON_CODE_RE.fullmatch(code):
        return CouponResolution(False, REASON_INVALID_FORMAT, None)
    promo = next(
        (p for p in promotions if p.code.upper() == code and not p.is_automatic),
        None,
    )
    if promo is None:
        return CouponResolution(False, REASON_NOT_FOUND, None)
    if promo.status != "active":
        return CouponResolution(False, REASON_INACTIVE, promo)
    if promo.starts_at_ms is not None and now_ms < promo.starts_at_ms:
        return CouponResolution(False, REASON_NOT_STARTED, promo)
    if promo.ends_at_ms is not None and now_ms > promo.ends_at_ms:
        return CouponResolution(False, REASON_EXPIRED, promo)
    if (
        promo.budget_limit is not None
        and promo.budget_used is not None
        and promo.budget_used >= promo.budget_limit
    ):
        return CouponResolution(False, REASON_BUDGET, promo)
    return CouponResolution(True, None, promo)


@dataclass(frozen=True)
class DiscountResult:
    discount_cop: int
    applicable_handles: list[str] = field(default_factory=list)
    applies_to_shipping: bool = False
    #: None = aplicó; "no_applicable_items" | "min_subtotal" | "unsupported"
    reason: str | None = None
    min_subtotal_cop: int | None = None


def _selects(promo: PromotionDTO, item: DiscountLineItem) -> bool:
    if not (promo.product_ids or promo.variant_ids or promo.collection_ids):
        return True
    if item.product_id and item.product_id in promo.product_ids:
        return True
    if item.variant_id and item.variant_id in promo.variant_ids:
        return True
    return bool(item.collection_id and item.collection_id in promo.collection_ids)


def compute_discount(
    promo: PromotionDTO,
    items: list[DiscountLineItem],
    *,
    shipping_cop: int = 0,
) -> DiscountResult:
    """Descuento en COP (entero) que produce `promo` sobre `items`.

    * percentage/fixed sobre `items`/`order`: se aplica al subtotal de las
      líneas seleccionadas (todas si la promo no filtra productos).
      `fixed`+`across` = monto único (tope: el subtotal aplicable);
      `fixed`+`each` = monto por unidad (tope: `max_quantity` unidades y el
      precio de cada unidad).
    * `shipping_methods`: descuenta el envío (tope: el envío).
    * `buyget`: no se calcula acá (unsupported) — el operador lo aplica.
    """
    if promo.discount_type not in ("percentage", "fixed"):
        return DiscountResult(0, reason="unsupported")

    subtotal = sum(it.unit_price_cop * it.quantity for it in items)
    if promo.min_subtotal_cop is not None and subtotal < promo.min_subtotal_cop:
        return DiscountResult(
            0, reason="min_subtotal", min_subtotal_cop=promo.min_subtotal_cop
        )

    if promo.target_type == "shipping_methods":
        if promo.discount_type == "percentage":
            amount = round(shipping_cop * promo.value / 100)
        else:
            amount = min(promo.value, shipping_cop)
        return DiscountResult(int(max(amount, 0)), applies_to_shipping=True)

    selected = [it for it in items if _selects(promo, it)]
    if not selected:
        return DiscountResult(0, reason="no_applicable_items")
    handles = [it.handle for it in selected]
    applicable_subtotal = sum(it.unit_price_cop * it.quantity for it in selected)

    if promo.discount_type == "percentage":
        amount = round(applicable_subtotal * promo.value / 100)
    elif promo.allocation == "each":
        remaining = promo.max_quantity if promo.max_quantity is not None else None
        amount = 0
        for it in selected:
            units = it.quantity if remaining is None else min(it.quantity, remaining)
            if units <= 0:
                continue
            amount += min(promo.value, it.unit_price_cop) * units
            if remaining is not None:
                remaining -= units
    else:
        amount = min(promo.value, applicable_subtotal)
    amount = int(min(max(amount, 0), applicable_subtotal))
    return DiscountResult(amount, applicable_handles=handles)


__all__ = [
    "COUPON_CODE_RE",
    "CouponResolution",
    "DiscountResult",
    "compute_discount",
    "normalize_coupon_code",
    "resolve_coupon",
]
