"""The five core metrics, as pure functions.

Every ratio guards its denominator: a zero denominator returns ``None`` — the
engine never divides by zero and never invents a number to fill the gap. All
arithmetic is exact (``Decimal``); rounding happens only at presentation time.
"""

from __future__ import annotations

from decimal import Decimal

from .models import DayMetrics


def _safe_ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def drop_off_rate(inline_link_clicks: int, conversations_started: int) -> Decimal | None:
    """1 - (conversations / clicks). Undefined when there were no clicks."""
    if inline_link_clicks == 0:
        return None
    return Decimal(1) - (Decimal(conversations_started) / Decimal(inline_link_clicks))


def cost_per_conversation_cop(spend_cop: int, conversations_started: int) -> Decimal | None:
    return _safe_ratio(spend_cop, conversations_started)


def mer(total_revenue_cop: int, spend_cop: int) -> Decimal | None:
    """Marketing Efficiency Ratio = revenue / spend."""
    return _safe_ratio(total_revenue_cop, spend_cop)


def global_cpa_cop(spend_cop: int, total_orders: int) -> Decimal | None:
    return _safe_ratio(spend_cop, total_orders)


def global_win_rate(total_orders: int, conversations_started: int) -> Decimal | None:
    return _safe_ratio(total_orders, conversations_started)


def compute_metrics(
    *,
    spend_cop: int,
    inline_link_clicks: int,
    conversations_started: int,
    total_orders: int,
    total_revenue_cop: int,
) -> DayMetrics:
    """Build the full metric set from one day's (or period's) raw totals."""
    return DayMetrics(
        drop_off_rate=drop_off_rate(inline_link_clicks, conversations_started),
        cost_per_conversation_cop=cost_per_conversation_cop(spend_cop, conversations_started),
        mer=mer(total_revenue_cop, spend_cop),
        global_cpa_cop=global_cpa_cop(spend_cop, total_orders),
        global_win_rate=global_win_rate(total_orders, conversations_started),
    )
