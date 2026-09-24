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
#: La promo tiene reglas que no pudimos leer: no se sabe a qué aplica.
REASON_SCOPE_UNRESOLVED = "scope_unresolved"


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
    if promo.scope_unresolved:
        return CouponResolution(False, REASON_SCOPE_UNRESOLVED, promo)
    return CouponResolution(True, None, promo)


@dataclass(frozen=True)
class LineDiscount:
    """`units` unidades de la línea `index` del pedido llevan
    `discount_unit_cop` pesos de descuento cada una."""

    index: int
    units: int
    discount_unit_cop: int
    #: Cupo por unidad que consumen (central de cupones); None = sin cupo.
    quota_id: str | None = None


@dataclass(frozen=True)
class DiscountResult:
    discount_cop: int
    applicable_handles: list[str] = field(default_factory=list)
    applies_to_shipping: bool = False
    #: None = aplicó; "no_applicable_items" | "min_subtotal" | "unsupported"
    reason: str | None = None
    min_subtotal_cop: int | None = None
    #: Reparto por unidad del descuento de productos: suma exacta
    #: `discount_cop`. Vacío si el cupón es de envío o no aplicó.
    line_discounts: tuple[LineDiscount, ...] = ()


def _selects(promo: PromotionDTO, item: DiscountLineItem) -> bool:
    # Condición por etiquetas: se suma (Y) a la de productos, como en Medusa.
    if promo.tag_values and not set(item.tags) & set(promo.tag_values):
        return False
    if not (promo.product_ids or promo.variant_ids or promo.collection_ids):
        return True
    if item.product_id and item.product_id in promo.product_ids:
        return True
    if item.variant_id and item.variant_id in promo.variant_ids:
        return True
    return bool(item.collection_id and item.collection_id in promo.collection_ids)


def _discounted_units(
    promo: PromotionDTO, indexed: list[tuple[int, DiscountLineItem]]
) -> dict[int, int]:
    """Unidades con descuento de cada línea, con la semántica de Medusa:

    * `each`: hasta `max_quantity` POR LÍNEA.
    * `once`: `max_quantity` en TODO el pedido, las unidades más baratas
      primero (sin tope no llega acá: `compute_discount` la rechaza).
    * el resto: todas las unidades.
    """
    if promo.allocation == "once":
        left = promo.max_quantity or 0
        units: dict[int, int] = {}
        for i, it in sorted(indexed, key=lambda pair: pair[1].unit_price_cop):
            units[i] = max(min(it.quantity, left), 0)
            left -= units[i]
        return units
    if promo.allocation == "each" and promo.max_quantity is not None:
        return {i: max(min(it.quantity, promo.max_quantity), 0) for i, it in indexed}
    return {i: max(it.quantity, 0) for i, it in indexed}


def _unit_discount(promo: PromotionDTO, unit_price_cop: int) -> int:
    """Descuento de UNA unidad, en pesos enteros y nunca más que su precio.

    Porcentaje: la mitad redondea hacia arriba. Fijo por unidad: el valor."""
    if promo.discount_type == "percentage":
        amount = (unit_price_cop * promo.value + 50) // 100
    else:
        amount = promo.value
    return max(min(amount, unit_price_cop), 0)


def _per_unit(
    promo: PromotionDTO, indexed: list[tuple[int, DiscountLineItem]]
) -> tuple[LineDiscount, ...]:
    """Porcentaje y fijo por unidad (`each`/`once`): cada unidad con descuento
    lleva el suyo."""
    units_by_line = _discounted_units(promo, indexed)
    lines: list[LineDiscount] = []
    for i, it in indexed:
        units = units_by_line[i]
        per_unit = _unit_discount(promo, it.unit_price_cop)
        if units > 0 and per_unit > 0:
            lines.append(LineDiscount(i, units, per_unit))
    return tuple(lines)


def _prorate(
    amount: int, indexed: list[tuple[int, DiscountLineItem]]
) -> tuple[LineDiscount, ...]:
    """Reparte un monto fijo entre las líneas según su subtotal, por unidad y
    en pesos enteros, sin perder pesos.

    Cada unidad lleva la parte entera de su proporción; los pesos que sobran
    van a la ÚLTIMA línea (a la anterior solo si ahí no caben: una unidad
    nunca baja de $0). Una línea cuyo monto no se divide exacto entre sus
    unidades sale en dos tramos que difieren en $1.
    """
    lines = [(i, it) for i, it in indexed if it.quantity > 0 and it.unit_price_cop > 0]
    total = sum(it.unit_price_cop * it.quantity for _, it in lines)
    if amount <= 0 or total <= 0:
        return ()
    line_totals = [it.quantity * (amount * it.unit_price_cop // total) for _, it in lines]
    remainder = amount - sum(line_totals)
    for k in reversed(range(len(lines))):
        it = lines[k][1]
        take = min(remainder, it.quantity * it.unit_price_cop - line_totals[k])
        line_totals[k] += take
        remainder -= take
    out: list[LineDiscount] = []
    for (i, it), line_total in zip(lines, line_totals):
        per_unit, extra = divmod(line_total, it.quantity)
        if per_unit > 0 and it.quantity > extra:
            out.append(LineDiscount(i, it.quantity - extra, per_unit))
        if extra > 0:
            out.append(LineDiscount(i, extra, per_unit + 1))
    return tuple(out)


def compute_discount(
    promo: PromotionDTO,
    items: list[DiscountLineItem],
    *,
    shipping_cop: int = 0,
) -> DiscountResult:
    """Descuento en COP (entero) que produce `promo` sobre `items`, con su
    reparto por unidad (`line_discounts`, que suma exacto `discount_cop`): es
    el precio que el pedido escribe en Medusa para cada línea.

    Aplica a las líneas seleccionadas (todas si la promo no filtra productos),
    con la semántica de `max_quantity` de Medusa:
    * `percentage`: cada unidad lleva su % redondeado a peso.
    * `fixed` + `each`/`once`: el monto por unidad (tope: el precio de cada
      unidad).
    * `each`: hasta `max_quantity` unidades POR LÍNEA; `once`: `max_quantity`
      unidades en todo el pedido, las más baratas primero.
    * `fixed` + `across`: monto único (tope: el subtotal aplicable)
      prorrateado por unidad; los pesos que sobran van a la última línea.
    * `shipping_methods`: descuenta el envío (tope: el envío), sin reparto
      por línea.
    * `buyget`: no se calcula acá (unsupported) — el operador lo aplica.
    """
    if promo.discount_type not in ("percentage", "fixed"):
        return DiscountResult(0, reason="unsupported")
    # `once` sin tope: Medusa no deja crearla; un snapshot así no se entiende.
    if promo.allocation == "once" and not promo.max_quantity:
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

    indexed = [(i, it) for i, it in enumerate(items) if _selects(promo, it)]
    if not indexed:
        return DiscountResult(0, reason="no_applicable_items")

    if promo.discount_type == "percentage" or promo.allocation in ("each", "once"):
        lines = _per_unit(promo, indexed)
    else:
        applicable_subtotal = sum(it.unit_price_cop * it.quantity for _, it in indexed)
        lines = _prorate(min(promo.value, applicable_subtotal), indexed)
    return DiscountResult(
        sum(d.units * d.discount_unit_cop for d in lines),
        applicable_handles=[it.handle for _, it in indexed],
        line_discounts=lines,
    )


__all__ = [
    "COUPON_CODE_RE",
    "CouponResolution",
    "DiscountResult",
    "LineDiscount",
    "compute_discount",
    "normalize_coupon_code",
    "resolve_coupon",
]
