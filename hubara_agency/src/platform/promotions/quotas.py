"""Cupo por unidad de un cupón — dominio PURO (sin I/O).

Un cupo dice "de este cupón, N unidades de este producto en este color y este
aroma". Las vendidas NO se guardan: se derivan de los pedidos de Medusa que
llevan la marca del cupo en la línea (`coupon_sales`), así lo cancelado, lo
borrado y lo de prueba deja de contar solo.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from src.platform.catalog.variant_attrs import match_option, normalize_label


@dataclass(frozen=True)
class PromoUnitQuota:
    id: str
    promotion_id: str
    code: str
    product_id: str
    handle: str
    title: str
    color: str | None
    aroma: str | None
    units: int
    created_at: str = ""
    created_by: str = ""


@dataclass(frozen=True)
class QuotaStatus:
    quota: PromoUnitQuota
    sold: int
    units_left: int
    oversold: bool


@dataclass(frozen=True)
class QuotaLine:
    product_id: str
    quantity: int
    unit_price_cop: int
    color: str | None = None
    aroma: str | None = None


def quota_statuses(
    quotas: list[PromoUnitQuota], sold: Mapping[str, int]
) -> list[QuotaStatus]:
    """Cuántas quedan de cada fila: `units − vendidas`, nunca negativo.

    `oversold` avisa que el operador bajó las unidades por debajo de lo que
    ya se vendió (la central lo muestra; el bot solo ve 0)."""
    out: list[QuotaStatus] = []
    for quota in quotas:
        n = max(int(sold.get(quota.id, 0)), 0)
        out.append(QuotaStatus(quota, n, max(quota.units - n, 0), n > quota.units))
    return out


REASON_QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True)
class QuotaGrant:
    line: int
    quota_id: str
    units: int
    discount_unit_cop: int


@dataclass(frozen=True)
class QuotaAllocation:
    grants: tuple[QuotaGrant, ...] = ()
    missing_attributes: tuple[int, ...] = ()

    @property
    def discount_cop(self) -> int:
        return sum(g.units * g.discount_unit_cop for g in self.grants)

    def units_on_line(self, line: int) -> int:
        return sum(g.units for g in self.grants if g.line == line)


def unit_discount_cop(unit_price_cop: int, percentage: int) -> int:
    """Descuento de UNA unidad en pesos enteros: la mitad sube (igual que el
    reparto de la Fase 0) y nunca más que el precio."""
    return max(min((unit_price_cop * percentage + 50) // 100, unit_price_cop), 0)


def _same(line_value: str | None, quota_value: str | None) -> bool | None:
    """True/False si el atributo coincide; None si el cupo lo exige y la
    línea no lo dice (falla cerrada)."""
    if quota_value is None:
        return True
    if not normalize_label(line_value or ""):
        return None
    return normalize_label(line_value or "") == normalize_label(quota_value)


def allocate_units(
    statuses: list[QuotaStatus], lines: list[QuotaLine], *, percentage: int
) -> QuotaAllocation:
    """Qué unidades de cada línea llevan el descuento del cupo.

    En orden de línea: unidades con descuento = mín(cantidad, quedan de esa
    combinación), y lo usado se descuenta de lo que queda (dos líneas iguales
    comparten el cupo). Lo que no alcanza va a precio normal (D2 parcial).
    Una línea de un producto con cupo que no dice el color/aroma que el cupo
    exige no recibe nada y queda en `missing_attributes`.
    """
    left = {s.quota.id: s.units_left for s in statuses}
    grants: list[QuotaGrant] = []
    missing: list[int] = []
    for index, line in enumerate(lines):
        pending = max(line.quantity, 0)
        unknown = False
        for status in statuses:
            quota = status.quota
            if quota.product_id != line.product_id or pending == 0:
                continue
            color, aroma = _same(line.color, quota.color), _same(line.aroma, quota.aroma)
            if color is None or aroma is None:
                unknown = True
                continue
            if not (color and aroma) or left[quota.id] <= 0:
                continue
            units = min(pending, left[quota.id])
            left[quota.id] -= units
            pending -= units
            grants.append(
                QuotaGrant(index, quota.id, units, unit_discount_cop(line.unit_price_cop, percentage))
            )
        if unknown and not any(g.line == index for g in grants):
            missing.append(index)
    return QuotaAllocation(tuple(grants), tuple(missing))


def quota_exhausted(statuses: list[QuotaStatus]) -> str | None:
    """`quota_exhausted` si el cupón tiene cupo y no queda ninguna unidad."""
    if statuses and all(s.units_left <= 0 for s in statuses):
        return REASON_QUOTA_EXHAUSTED
    return None


@dataclass(frozen=True)
class QuotaProduct:
    """Lo que la validación necesita de un producto del catálogo."""

    product_id: str
    handle: str
    title: str
    colors: list[str]
    aromas: list[str]


@dataclass(frozen=True)
class QuotaRowError:
    row: int
    field: str
    message: str


def quota_id_for(promotion_id: str, product_id: str, color: str | None, aroma: str | None) -> str:
    """Id ESTABLE de una combinación: lo lleva cada línea vendida
    (`coupon_quota_id`), así re-guardar las filas no pierde las vendidas."""
    key = "|".join(
        (promotion_id, product_id, normalize_label(color or ""), normalize_label(aroma or ""))
    )
    return "q_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


_ATTR_WORDS = {"color": ("color", "colores"), "aroma": ("aroma", "aromas")}


def _attribute(
    index: int, field: str, raw: Any, options: list[str], title: str
) -> tuple[str | None, QuotaRowError | None]:
    """Valor canónico del atributo, o el error de la fila."""
    one, many = _ATTR_WORDS[field]
    given = str(raw).strip() if raw is not None else ""
    if not options:
        if given:
            return None, QuotaRowError(index, field, f"{title} no tiene {many}; dejá el {one} vacío.")
        return None, None
    if not given:
        return None, QuotaRowError(index, field, f"Elegí el {one} de {title}.")
    canonical = match_option(given, options)
    if canonical is None:
        return None, QuotaRowError(
            index, field, f'"{given}" no es un {one} de {title} ({", ".join(options)}).'
        )
    return canonical, None


def validate_quota_rows(
    rows: list[Any],
    *,
    promotion_id: str,
    code: str,
    coupon_products: tuple[str, ...] | None,
    products: Mapping[str, QuotaProduct],
    actor: str,
    now_iso: str,
) -> tuple[list[PromoUnitQuota], list[QuotaRowError]]:
    """Las filas que guarda el operador → cupos validados, o errores POR FILA.

    El producto tiene que estar en el cupón (o el cupón es de todo el
    catálogo); color y aroma salen de las listas cerradas del producto (sus
    etiquetas): obligatorios si el producto tiene esa lista, vacíos si no.
    """
    quotas: list[PromoUnitQuota] = []
    errors: list[QuotaRowError] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        row = row if isinstance(row, dict) else {}
        product_id = str(row.get("product_id") or "").strip()
        product = products.get(product_id)
        if product is None or (coupon_products is not None and product_id not in coupon_products):
            errors.append(QuotaRowError(index, "product_id", "Ese producto no está en el cupón."))
            continue
        color, color_error = _attribute(index, "color", row.get("color"), product.colors, product.title)
        aroma, aroma_error = _attribute(index, "aroma", row.get("aroma"), product.aromas, product.title)
        units = row.get("units")
        units_error = (
            None
            if isinstance(units, int) and not isinstance(units, bool) and units >= 1
            else QuotaRowError(index, "units", "Las unidades son un número entero desde 1.")
        )
        row_errors = [e for e in (color_error, aroma_error, units_error) if e]
        if row_errors:
            errors.extend(row_errors)
            continue
        qid = quota_id_for(promotion_id, product_id, color, aroma)
        if qid in seen:
            errors.append(
                QuotaRowError(index, "product_id", "Esa combinación está repetida; sumá las unidades en una fila.")
            )
            continue
        seen.add(qid)
        quotas.append(
            PromoUnitQuota(
                id=qid,
                promotion_id=promotion_id,
                code=code,
                product_id=product_id,
                handle=product.handle,
                title=product.title,
                color=color,
                aroma=aroma,
                units=int(units),
                created_at=now_iso,
                created_by=actor,
            )
        )
    return quotas, errors
