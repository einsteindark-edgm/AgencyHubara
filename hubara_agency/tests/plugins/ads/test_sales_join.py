"""El join de ventas del análisis IA (2026-07-10): `manual_sales` del
analysis-input sale de MEDUSA (la verdad del pago — diseño acordado: los labels
del vault congelan montos pre-pago), agregado por día en el shape que el pod
`ads-analytics` consume: {"sales": [{date, total_orders, total_revenue}]}.

Sin esto el pod recibía `sales: []` y el análisis terminaba en
`verdict: insufficient_data` con el reporte "sin días en común (no hay blend
posible)" — caso real 424d6647.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.plugins.ads.sales_join import manual_sales_from_orders


def _order(*, created: str, total: int = 50000, pay_status: str = "paid",
           status: str = "delivered", is_draft: bool = False) -> SimpleNamespace:
    """OrderSummaryDTO liviano (duck-typed) — created es 'YYYY-MM-DDTHH:MM' UTC."""
    import datetime

    dt = datetime.datetime.fromisoformat(created).replace(tzinfo=datetime.timezone.utc)
    return SimpleNamespace(
        created_at_ms=int(dt.timestamp() * 1000),
        total_cop=total,
        pay_status=pay_status,
        status=status,
        is_draft=is_draft,
    )


def test_agrupa_las_pagadas_por_dia_en_el_shape_del_pod() -> None:
    orders = [
        _order(created="2026-06-15T10:00", total=600000),
        _order(created="2026-06-15T18:30", total=150000),
        _order(created="2026-06-16T09:00", total=90000),
    ]
    out = manual_sales_from_orders(orders, since="2026-06-01", until="2026-06-30")
    assert out == {"sales": [
        {"date": "2026-06-15", "total_orders": 2, "total_revenue": 750000},
        {"date": "2026-06-16", "total_orders": 1, "total_revenue": 90000},
    ]}


def test_solo_cuentan_las_pagadas_no_drafts_no_canceladas() -> None:
    # Medusa = verdad del PAGO: pending/partial/refund no son venta cerrada;
    # drafts son carritos; cancelled no es venta aunque esté paid.
    orders = [
        _order(created="2026-06-15T10:00", total=100000),
        _order(created="2026-06-15T11:00", total=999999, pay_status="pending"),
        _order(created="2026-06-15T12:00", total=999999, pay_status="refund"),
        _order(created="2026-06-15T13:00", total=999999, is_draft=True),
        _order(created="2026-06-15T14:00", total=999999, status="cancelled"),
    ]
    out = manual_sales_from_orders(orders, since="2026-06-01", until="2026-06-30")
    assert out == {"sales": [
        {"date": "2026-06-15", "total_orders": 1, "total_revenue": 100000},
    ]}


def test_respeta_la_ventana_del_analisis() -> None:
    orders = [
        _order(created="2026-05-31T23:00"),  # antes de since → fuera
        _order(created="2026-06-01T00:30", total=70000),
        _order(created="2026-06-30T23:00", total=30000),
        _order(created="2026-07-01T01:00"),  # después de until → fuera
    ]
    out = manual_sales_from_orders(orders, since="2026-06-01", until="2026-06-30")
    assert [s["date"] for s in out["sales"]] == ["2026-06-01", "2026-06-30"]
    assert sum(s["total_revenue"] for s in out["sales"]) == 100000


def test_sin_ordenes_devuelve_sales_vacio() -> None:
    assert manual_sales_from_orders([], since="2026-06-01", until="2026-06-30") == {"sales": []}
