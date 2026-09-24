"""Central de cupones — traducción PURA entre el dominio del SDK y el JSON
del dashboard (sin I/O; el router solo orquesta ports).
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.sdk.connectorkit import (
    CouponResults,
    CouponSpecError,
    CouponView,
    DiscountLineItem,
    PromotionDTO,
    QuotaSheet,
    QuotaStatus,
    compute_discount,
)

_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_BOGOTA = timezone(timedelta(hours=-5))  # Colombia: sin horario de verano

#: Campos del formulario que se pueden editar (y auditar) en un cupón.
EDITABLE_FIELDS = ("code", "campaign_name", "percentage", "products", "starts_on", "ends_on")


def day_label(day: date) -> str:
    """`2026-09-27` → "27 de septiembre" (como se lo dice la campaña al cliente)."""
    return f"{day.day} de {_MONTHS[day.month - 1]}"


def coupon_terms(promo: PromotionDTO) -> dict[str, Any]:
    """Lo que la campaña anuncia del cupón, sacado del cupón (no del
    operador): el % si es de porcentaje y el último día incluido, en hora de
    Bogotá ("27 de septiembre" para una campaña que cierra el 28 a las 00:00)."""
    is_percent = promo.discount_type == "percentage" and promo.target_type == "items"
    # Un cupón que no es de porcentaje no anuncia un % (ni el que tipeó el operador).
    terms: dict[str, Any] = {"percent": promo.value if is_percent else 0}
    if promo.ends_at_ms is not None:
        last = datetime.fromtimestamp((promo.ends_at_ms - 1) / 1000, tz=_BOGOTA).date()
        terms["valid_until"] = day_label(last)
    return terms


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _products(products: tuple[str, ...] | None) -> list[str] | str:
    return list(products) if products else "all"


def coupon_json(view: CouponView, *, units: dict[str, int | None] | None = None) -> dict[str, Any]:
    """Lo que la central muestra de un cupón (lista y detalle)."""
    return {
        "promotion_id": view.promotion_id,
        "campaign_id": view.campaign_id,
        "code": view.code,
        "campaign_name": view.campaign_name,
        "percentage": view.percentage,
        "products": _products(view.products),
        "starts_on": _iso(view.starts_on),
        "ends_on": _iso(view.ends_on),
        "ends_on_label": day_label(view.ends_on) if view.ends_on else None,
        "status": view.status,
        "state": view.state,
        "manageable": view.manageable,
        "unmanageable_reason": view.unmanageable_reason,
        # El cupo es de Hubara: se puede poner a cualquier cupón de
        # porcentaje, aunque en Medusa sea de solo lectura.
        "accepts_units": view.percentage is not None,
        "units": units,
    }


def units_summary(board: list[QuotaStatus] | None, sheet: QuotaSheet) -> dict[str, int | None] | None:
    """`{total, left}` del cupo; `left` None si no se pudo leer lo vendido."""
    if not sheet.quotas:
        return None
    total = sum(q.units for q in sheet.quotas)
    left = None if board is None else sum(s.units_left for s in board)
    return {"total": total, "left": left}


def units_json(board: list[QuotaStatus], sheet: QuotaSheet) -> dict[str, Any]:
    return {
        "rows": [
            {
                "id": s.quota.id,
                "product_id": s.quota.product_id,
                "handle": s.quota.handle,
                "title": s.quota.title,
                "color": s.quota.color,
                "aroma": s.quota.aroma,
                "units": s.quota.units,
                "sold": s.sold,
                "units_left": s.units_left,
                "oversold": s.oversold,
                "created_by": s.quota.created_by,
            }
            for s in board
        ],
        "show_units_left": sheet.show_units_left,
    }


def results_json(results: CouponResults) -> dict[str, Any]:
    return {
        "orders": results.orders,
        "discount_cop": results.discount_cop,
        "quota_units": results.quota_units,
        "sales": [asdict(s) for s in results.sales],
    }


def spec_patch(view: CouponView, patch: dict[str, Any]) -> dict[str, Any]:
    """El formulario COMPLETO resultante de aplicar `patch` al cupón actual
    (para validarlo entero con `parse_coupon_spec`).

    El código solo cambia mientras el cupón es borrador."""
    unknown = sorted(set(patch) - set(EDITABLE_FIELDS))
    if unknown:
        raise CouponSpecError(unknown[0], "Ese campo no se edita desde la central.")
    current: dict[str, Any] = {
        "code": view.code,
        "campaign_name": view.campaign_name or view.code,
        "percentage": view.percentage,
        "products": _products(view.products),
        "starts_on": _iso(view.starts_on),
        "ends_on": _iso(view.ends_on),
        # El estado va por su propio endpoint; `parse_coupon_spec` lo exige
        # entre draft|active, así que se valida con uno de esos.
        "status": "draft" if view.status == "draft" else "active",
    }
    merged = {**current, **patch}
    if str(merged["code"] or "").strip().upper() != view.code and view.status != "draft":
        raise CouponSpecError(
            "code", "El código solo se cambia mientras el cupón está en borrador."
        )
    return merged


def audit_diff(before: CouponView, after: CouponView) -> dict[str, list[Any]]:
    """`{campo: [antes, después]}` de lo que cambió (para el registro)."""
    b = coupon_json(before)
    a = coupon_json(after)
    return {f: [b[f], a[f]] for f in EDITABLE_FIELDS if b[f] != a[f]}


def coupon_product_ids(promo: PromotionDTO, products: list[Any]) -> tuple[str, ...]:
    """Los productos del catálogo que el cupón REALMENTE descuenta (productos
    Y etiquetas, con la semántica de Medusa). Es la lista contra la que se
    validan las filas del cupo."""
    per_unit = replace(promo, min_subtotal_cop=None)
    out: list[str] = []
    for product in products:
        line = DiscountLineItem(
            handle=str(getattr(product, "handle", "") or ""),
            quantity=1,
            unit_price_cop=100_000,
            product_id=str(getattr(product, "id", "") or "") or None,
            tags=tuple(str(t) for t in getattr(product, "tags", None) or []),
        )
        if compute_discount(per_unit, [line]).discount_cop > 0:
            out.append(str(product.id))
    return tuple(out)
