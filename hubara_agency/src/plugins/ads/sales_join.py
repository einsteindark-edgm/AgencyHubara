"""Join de ventas del análisis IA — `manual_sales` para el pod `ads-analytics`.

Medusa = la verdad del PAGO (diseño acordado 2026-07-09: los labels del vault
congelan montos pre-pago). Acá la capa PURA: órdenes → agregado diario en el
shape que el pod consume. El fetch vive en el endpoint (`api/meta_oauth.py`)
vía `src.sdk.connectorkit.get_order_query_port`.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _order_date(order: Any) -> str:
    """`created_at_ms` (epoch ms, UTC) → 'YYYY-MM-DD' (el eje del blend del pod)."""
    return datetime.fromtimestamp(order.created_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _is_sale(order: Any) -> bool:
    """Venta CERRADA = pagada (Medusa `pay_status == 'paid'`), no draft (carrito),
    no cancelada. pending/partial/refund NO son revenue confirmado."""
    return (
        getattr(order, "pay_status", None) == "paid"
        and not getattr(order, "is_draft", False)
        and getattr(order, "status", None) != "cancelled"
    )


def manual_sales_from_orders(orders: list[Any], *, since: str, until: str) -> dict:
    """Órdenes (OrderSummaryDTO) → `{"sales": [{date, total_orders, total_revenue}]}`
    con SOLO las pagadas dentro de la ventana [since, until] (fechas ISO, inclusive),
    agregadas por día y ordenadas — el shape exacto que el pod `ads-analytics`
    blendea contra los insights diarios de Meta."""
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"total_orders": 0, "total_revenue": 0})
    for order in orders:
        if not _is_sale(order):
            continue
        day = _order_date(order)
        if not (since <= day <= until):  # comparación lexicográfica ISO — correcta
            continue
        by_day[day]["total_orders"] += 1
        by_day[day]["total_revenue"] += int(getattr(order, "total_cop", 0) or 0)
    return {
        "sales": [
            {"date": day, "total_orders": agg["total_orders"], "total_revenue": agg["total_revenue"]}
            for day, agg in sorted(by_day.items())
        ]
    }
