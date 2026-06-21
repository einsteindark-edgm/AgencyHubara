"""Lógica PURA de `merge-by-date` — G-AGNOSTIC: solo stdlib. Inner-join de dos feeds
diarios (insights ya colapsados a nivel cuenta + ventas) por fecha. Las fechas que
aparecen en UN solo feed se SURFACEAN (meta_only / sales_only), nunca se dropean en
silencio — "truncar en silencio se lee como 'cubrí todo' cuando no fue así". Fecha
duplicada = error fuerte. Portada de ads-analytics-engine/src/ads_engine/merge.py.
"""
from __future__ import annotations


def _index_by_date(rows: list, kind: str) -> dict:
    out: dict = {}
    for row in rows:
        d = row["date"]
        if d in out:
            raise ValueError(f"fila {kind} duplicada para la fecha {d}")
        out[d] = row
    return out


def run(*, payload: dict) -> dict:
    """payload = {insights:[{date, spend_cop, inline_link_clicks, conversations_started}],
    sales:[{date, total_orders, total_revenue_cop}]} → días en común + fechas sin match."""
    meta = _index_by_date(payload.get("insights", []), "insight")
    sale = _index_by_date(payload.get("sales", []), "venta")
    common = sorted(set(meta) & set(sale))
    days = [
        {
            "date": d,
            "spend_cop": meta[d]["spend_cop"],
            "inline_link_clicks": meta[d]["inline_link_clicks"],
            "conversations_started": meta[d]["conversations_started"],
            "total_orders": sale[d]["total_orders"],
            "total_revenue_cop": sale[d]["total_revenue_cop"],
        }
        for d in common
    ]
    return {
        "days": days,
        "meta_only": sorted(set(meta) - set(sale)),
        "sales_only": sorted(set(sale) - set(meta)),
    }
