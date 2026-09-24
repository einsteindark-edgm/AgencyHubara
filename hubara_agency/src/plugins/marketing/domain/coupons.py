"""Central de cupones — traducción PURA entre el dominio del SDK y el JSON
del dashboard (sin I/O; el router solo orquesta ports). También el texto de
por qué el cupón de una campaña no sirve (lo usan la API y el envío).
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


#: Por qué el cupón que anuncia la campaña no sirve — para el operador
#: ("El cupón AMOR26 …"). Motivos de `resolve_coupon`.
_COUPON_PROBLEMS = {
    "invalid_format": "no tiene una forma válida (solo letras y números)",
    "not_found": "no existe en Medusa — créalo en Marketing → Cupones o elige uno de los vigentes",
    "inactive": "está pausado en Medusa",
    "not_started": "todavía no empieza a regir en Medusa",
    "expired": "ya venció en Medusa",
    "budget_exhausted": "ya agotó sus usos en Medusa",
    "scope_unresolved": "tiene reglas que no pude leer en Medusa (no sé a qué productos aplica)",
}


def campaign_coupon_problem(reason: str | None, promotion: Any = None, *, scheduled: bool = False) -> str:
    """El motivo, para el operador. `scheduled`: se validó para la hora de un
    envío programado (no para ahora)."""
    if reason == "inactive" and getattr(promotion, "status", "") == "draft":
        return "está en borrador (actívalo en Marketing → Cupones)"
    if scheduled and reason == "expired":
        return "ya no rige a la hora del envío programado (vence antes)"
    if scheduled and reason == "not_started":
        return "todavía no rige a la hora del envío programado"
    return _COUPON_PROBLEMS.get(reason or "", "no se puede usar")


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
    """Filas del cupo con vendidas/quedan. `updated_at` es la versión de lo
    guardado (C-5): el editor la devuelve como `expected_updated_at`."""
    return {
        "updated_at": sheet.updated_at or None,
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


def coupon_form(view: CouponView) -> dict[str, Any]:
    """El formulario del cupón tal como está en Medusa (al editarlo, lo que
    no cambia no se vuelve a validar: `parse_coupon_spec(…, current=…)`)."""
    return {
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


def spec_patch(view: CouponView, patch: dict[str, Any]) -> dict[str, Any]:
    """El formulario COMPLETO resultante de aplicar `patch` al cupón actual
    (para validarlo entero con `parse_coupon_spec`).

    El código solo cambia mientras el cupón es borrador."""
    unknown = sorted(set(patch) - set(EDITABLE_FIELDS))
    if unknown:
        raise CouponSpecError(unknown[0], "Ese campo no se edita desde la central.")
    merged = {**coupon_form(view), **patch}
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


def intended_diff(before: CouponView, spec: Any) -> dict[str, list[Any]]:
    """`{campo: [antes, lo pedido]}`: lo que el operador QUISO cambiar (para
    el registro cuando no se sabe cómo quedó el cupón en Medusa). `spec` es
    el `CouponSpec` validado."""
    b = coupon_json(before)
    wanted = {
        "code": spec.code,
        "campaign_name": spec.campaign_name,
        "percentage": spec.percentage,
        "products": _products(spec.products),
        "starts_on": _iso(spec.starts_on),
        "ends_on": _iso(spec.ends_on),
    }
    return {f: [b[f], wanted[f]] for f in EDITABLE_FIELDS if b[f] != wanted[f]}


def quota_row_label(quota: Any) -> str:
    """Una fila del cupo para el registro: "Cubo Love · Rosado · Café: 5"."""
    return f"{quota.title} · {quota.color or '—'} · {quota.aroma or '—'}: {quota.units}"


#: Margen de la lectura ÚNICA de la lista, mayor que el de `quota_board`
#: (1 h) para que cada hoja pueda contar desde su propio inicio.
_SCAN_MARGIN = timedelta(hours=2)
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def scan_since(sheets: list[QuotaSheet]) -> datetime:
    """Desde cuándo leer los pedidos para contar TODAS las hojas de la lista
    en una sola lectura: el inicio más viejo (`counting_since`, o la fila más
    vieja si la hoja es anterior a ese campo) con margen."""
    starts: list[datetime] = []
    for sheet in sheets:
        fixed = _instant(sheet.counting_since)
        rows = [d for d in (_instant(q.created_at) for q in sheet.quotas) if d is not None]
        starts.append(fixed or (min(rows) if rows else _EPOCH))
    return (min(starts) if starts else _EPOCH) - _SCAN_MARGIN


def created_since(orders: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Los pedidos creados desde `since` (uno sin fecha legible cuenta: mejor
    contar de más que vender de más)."""
    return [o for o in orders if (_instant(o.get("created_at")) or since) >= since]


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
