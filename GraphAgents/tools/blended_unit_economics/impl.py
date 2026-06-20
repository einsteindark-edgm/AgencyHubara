"""Lógica PURA de `blended-unit-economics` — G-AGNOSTIC: solo stdlib (decimal).
Las 5 métricas CTWA desde totales crudos. Aritmética EXACTA (Decimal); denominador
0 → None (nunca un número inventado). El redondeo es cosa de presentación, no de acá.

Portada de ads-analytics-engine/src/ads_engine/metrics.py.
"""
from __future__ import annotations

from decimal import Decimal


def _safe_ratio(num: int, den: int) -> Decimal | None:
    return None if den == 0 else Decimal(num) / Decimal(den)


def _drop_off(clicks: int, conv: int) -> Decimal | None:
    if clicks == 0:
        return None
    return Decimal(1) - (Decimal(conv) / Decimal(clicks))


def _s(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def run(*, payload: dict) -> dict:
    """payload = totales crudos del día/periodo → las 5 métricas (Decimal como string, o None).

    Undefined (denominador 0) = None, JAMÁS un número adivinado. `mer` de revenue 0
    sobre spend real es un 0 REAL, no None.
    """
    spend = payload["spend_cop"]
    clicks = payload["inline_link_clicks"]
    conv = payload["conversations_started"]
    orders = payload["total_orders"]
    revenue = payload["total_revenue_cop"]
    return {
        "drop_off_rate": _s(_drop_off(clicks, conv)),
        "cost_per_conversation_cop": _s(_safe_ratio(spend, conv)),
        "mer": _s(_safe_ratio(revenue, spend)),
        "global_cpa_cop": _s(_safe_ratio(spend, orders)),
        "global_win_rate": _s(_safe_ratio(orders, conv)),
    }
