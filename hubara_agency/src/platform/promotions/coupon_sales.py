"""Ventas de un cupón, DERIVADAS de los pedidos de Medusa (sin contador).

Las vendidas de cada cupo son las unidades de las líneas que llevan
`metadata.coupon_quota_id`; lo cancelado (en Medusa o en Hubara), lo borrado y
lo de prueba deja de contar solo. El mapeo es puro; `CouponSalesReader` solo
trae los pedidos y drafts desde el inicio de la campaña.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from src.platform.orders.state import META_KEY_STAGE, META_KEY_TEST_ORDER
from src.platform.promotions.port import PromotionsUnavailableError
from src.platform.promotions.quota_store import QuotaSheet
from src.platform.promotions.quotas import QuotaStatus, quota_statuses

#: Marca de la línea que consumió un cupo (la escribe el pedido con cupo).
LINE_QUOTA_KEY = "coupon_quota_id"
#: Claves de línea de la Fase 0 (pedido #44): el cupón y su descuento por unidad.
LINE_COUPON_KEY = "coupon_code"
LINE_DISCOUNT_UNIT_KEY = "discount_unit_cop"

#: Lo mínimo para contar: la línea con su metadata y el estado del pedido.
ORDER_FIELDS = "id,display_id,status,created_at,canceled_at,metadata,*items"
_CLOCK_MARGIN = timedelta(hours=1)

#: Un pedido por (`metadata.session_key`, `metadata.order_fingerprint`): lo
#: que escribe el adapter de Medusa en cada draft.
OrderKey = tuple[str, str]


@dataclass(frozen=True)
class CouponSale:
    order_id: str
    display_id: int | None
    created_at: str
    is_draft: bool
    quota_units: int
    discount_cop: int


@dataclass(frozen=True)
class CouponResults:
    orders: int
    discount_cop: int
    quota_units: int
    sales: tuple[CouponSale, ...]


def _counts(order: dict[str, Any]) -> bool:
    """¿Este pedido cuenta? No si está cancelado (en Medusa o en Hubara),
    borrado o marcado de prueba."""
    meta = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
    if order.get("status") == "canceled" or order.get("canceled_at") or order.get("deleted_at"):
        return False
    if meta.get(META_KEY_STAGE) == "cancelled":
        return False
    return meta.get(META_KEY_TEST_ORDER) is not True


def _live(orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pedidos que cuentan, una vez cada uno (un draft recién convertido
    puede aparecer en las dos listas)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        oid = str(order.get("id") or "")
        if oid in seen or not _counts(order):
            continue
        seen.add(oid)
        out.append(order)
    return out


def _lines(order: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for item in order.get("items") or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        try:
            qty = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        out.append((max(qty, 0), meta))
    return out


def _order_key(order: dict[str, Any]) -> OrderKey:
    meta = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
    return (str(meta.get("session_key") or ""), str(meta.get("order_fingerprint") or ""))


def sold_units_by_quota(
    orders: Iterable[dict[str, Any]], *, exclude: Optional[set[OrderKey]] = None
) -> dict[str, int]:
    """Unidades vendidas de cada cupo: Σ cantidad de las líneas con su marca.

    `exclude`: pedidos (sesión, fingerprint) que NO cuentan — el reintento de
    un pedido cuyo draft sí llegó a Medusa no se cuenta a sí mismo (L-28)."""
    sold: dict[str, int] = {}
    for order in _live(orders):
        if exclude and _order_key(order) in exclude:
            continue
        for qty, meta in _lines(order):
            quota_id = meta.get(LINE_QUOTA_KEY)
            if quota_id:
                sold[str(quota_id)] = sold.get(str(quota_id), 0) + qty
    return sold


def _to_int(raw: Any) -> int:
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return 0


def coupon_results(code: str, orders: Iterable[dict[str, Any]]) -> CouponResults:
    """Resultados del cupón: pedidos, descuento total y unidades del cupo.

    El descuento sale de las líneas (`discount_unit_cop` × cantidad); un
    pedido anterior a la Fase 0 solo trae `metadata.discount_cop`."""
    wanted = code.strip().upper()
    sales: list[CouponSale] = []
    for order in _live(orders):
        meta = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
        lines = [
            (qty, m) for qty, m in _lines(order)
            if str(m.get(LINE_COUPON_KEY) or "").upper() == wanted
        ]
        if not lines and str(meta.get(LINE_COUPON_KEY) or "").upper() != wanted:
            continue
        discount = sum(qty * _to_int(m.get(LINE_DISCOUNT_UNIT_KEY)) for qty, m in lines)
        if not lines:
            discount = _to_int(meta.get("discount_cop"))
        display_id = order.get("display_id")
        sales.append(
            CouponSale(
                order_id=str(order.get("id")),
                display_id=display_id if isinstance(display_id, int) else None,
                created_at=str(order.get("created_at") or ""),
                is_draft=order.get("status") == "draft",
                quota_units=sum(qty for qty, m in lines if m.get(LINE_QUOTA_KEY)),
                discount_cop=discount,
            )
        )
    return CouponResults(
        orders=len(sales),
        discount_cop=sum(s.discount_cop for s in sales),
        quota_units=sum(s.quota_units for s in sales),
        sales=tuple(sales),
    )


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _created(order: dict[str, Any]) -> datetime | None:
    return _parse_iso(order.get("created_at"))


async def quota_board(
    sheet: QuotaSheet, reader: Any, *, exclude: Optional[set[OrderKey]] = None
) -> list[QuotaStatus]:
    """Cuántas quedan de cada fila del cupo, leyendo lo vendido de Medusa.

    Una línea con la marca de un cupo no puede ser anterior al primer
    guardado de filas (`counting_since`, que nunca avanza): desde ahí se leen
    los pedidos, sin depender de las fechas de la campaña que el operador
    puede mover. Sin filas no lee nada. `exclude`: ver `sold_units_by_quota`."""
    if not sheet.quotas:
        return []
    fixed = _parse_iso(sheet.counting_since)
    starts = [d for d in (_parse_iso(q.created_at) for q in sheet.quotas) if d is not None]
    since = fixed or (min(starts) if starts else datetime(2000, 1, 1, tzinfo=timezone.utc))
    # Margen por la diferencia de reloj entre este host y Medusa.
    since -= _CLOCK_MARGIN
    sold = await (
        reader.sold_units(since=since, exclude=exclude) if exclude else reader.sold_units(since=since)
    )
    return quota_statuses(list(sheet.quotas), sold)


class UnavailableCouponSalesReader:
    """Sin Medusa configurado: no se pueden contar las vendidas (falla cerrada)."""

    async def orders_since(self, since: datetime) -> list[dict[str, Any]]:
        raise PromotionsUnavailableError("Medusa no está configurado en este deployment")

    async def sold_units(
        self, *, since: datetime, exclude: Optional[set[OrderKey]] = None
    ) -> dict[str, int]:
        return sold_units_by_quota(await self.orders_since(since), exclude=exclude)

    async def results(self, code: str, *, since: datetime) -> CouponResults:
        return coupon_results(code, await self.orders_since(since))


class CouponSalesReader:
    """Trae de Medusa los pedidos y drafts creados desde el inicio de la
    campaña (del más nuevo al más viejo, cortando al pasar `since`)."""

    def __init__(self, client: Any, *, page_size: int = 100) -> None:
        self._client = client
        self._page_size = page_size

    async def orders_since(self, since: datetime) -> list[dict[str, Any]]:
        try:
            orders, drafts = await asyncio.gather(
                self._scan(self._client.list_orders, "orders", since),
                self._scan(self._client.list_draft_orders, "draft_orders", since),
            )
        except PromotionsUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — el vendor no cruza el port
            raise PromotionsUnavailableError(f"no pude leer los pedidos: {exc}") from exc
        return orders + drafts

    async def sold_units(
        self, *, since: datetime, exclude: Optional[set[OrderKey]] = None
    ) -> dict[str, int]:
        return sold_units_by_quota(await self.orders_since(since), exclude=exclude)

    async def results(self, code: str, *, since: datetime) -> CouponResults:
        return coupon_results(code, await self.orders_since(since))

    async def _scan(self, list_page: Any, key: str, since: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await list_page(
                limit=self._page_size, offset=offset, order="-created_at", fields=ORDER_FIELDS
            )
            rows = [r for r in page.get(key) or [] if isinstance(r, dict)]
            fresh = [r for r in rows if (_created(r) or since) >= since]
            out.extend(fresh)
            offset += len(rows)
            count = page.get("count")
            # Corta al pasar `since`, con una página corta o cuando `count`
            # dice que no hay más. Sin `count` sigue paginando: cortar antes
            # contaría menos vendidas (el cupo vendería de más).
            if (
                len(fresh) < len(rows)
                or len(rows) < self._page_size
                or (isinstance(count, int) and offset >= count)
            ):
                return out
