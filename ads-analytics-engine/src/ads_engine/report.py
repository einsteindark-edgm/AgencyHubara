"""Render the blended report — a Markdown table + a JSON-able dict.

Every number rendered here comes straight from the engine's metrics; nothing is
re-derived ad hoc. Period rows aggregate the RAW totals first and then compute
the metrics on the totals (the correct blended way — not an average of ratios).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .diagnosis import diagnose
from .merge import MergeResult
from .metrics import compute_metrics
from .models import DayMetrics, Diagnosis


def _q_cop(value: Decimal | int) -> int:
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fmt_cop(value: Decimal | int | None) -> str:
    if value is None:
        return "—"
    return "$" + f"{_q_cop(value):,}".replace(",", ".")


def _fmt_pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(Decimal(value) * 100).quantize(Decimal('0.1'))}%"


def _fmt_ratio(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{Decimal(value).quantize(Decimal('0.01'))}"


def _dec_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class _Period:
    spend_cop: int
    inline_link_clicks: int
    conversations: int
    orders: int
    revenue_cop: int
    metrics: DayMetrics
    diagnosis: Diagnosis


def _period(days: list) -> _Period | None:
    if not days:
        return None
    spend = sum(d.spend_cop for d in days)
    clicks = sum(d.inline_link_clicks for d in days)
    conv = sum(d.messaging_conversations_started for d in days)
    orders = sum(d.total_orders for d in days)
    revenue = sum(d.total_revenue_cop for d in days)
    metrics = compute_metrics(
        spend_cop=spend,
        inline_link_clicks=clicks,
        conversations_started=conv,
        total_orders=orders,
        total_revenue_cop=revenue,
    )
    return _Period(spend, clicks, conv, orders, revenue, metrics, diagnose(metrics))


_HEADER = (
    "| Fecha | Spend | Clicks | Conversaciones | Drop-off | Costo/Conv | "
    "Órdenes | MER | CPA global | Win rate | Recomendación |\n"
    "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|"
)


def _row(label, spend, clicks, conv, drop, cpc, orders, mer_v, cpa, win, rec) -> str:
    return (
        f"| {label} | {_fmt_cop(spend)} | {clicks} | {conv} | {_fmt_pct(drop)} | "
        f"{_fmt_cop(cpc)} | {orders} | {_fmt_ratio(mer_v)} | {_fmt_cop(cpa)} | "
        f"{_fmt_pct(win)} | {rec} |"
    )


def render_markdown(result: MergeResult) -> str:
    lines = ["## Hubara — Ads Analytics (CTWA · blended por día)", "", _HEADER]
    for d in result.days:
        m = d.metrics
        lines.append(
            _row(
                d.date.isoformat(),
                d.spend_cop,
                d.inline_link_clicks,
                d.messaging_conversations_started,
                m.drop_off_rate,
                m.cost_per_conversation_cop,
                d.total_orders,
                m.mer,
                m.global_cpa_cop,
                m.global_win_rate,
                d.diagnosis.recommendation.value,
            )
        )

    period = _period(result.days)
    if period:
        m = period.metrics
        lines.append(
            _row(
                "**TOTAL**",
                period.spend_cop,
                period.inline_link_clicks,
                period.conversations,
                m.drop_off_rate,
                m.cost_per_conversation_cop,
                period.orders,
                m.mer,
                m.global_cpa_cop,
                m.global_win_rate,
                period.diagnosis.recommendation.value,
            )
        )

    lines.append("")
    if period:
        flags = []
        if period.diagnosis.high_friction:
            flags.append("fricción alta (drop-off > 40%)")
        if period.diagnosis.poor_profitability:
            flags.append("rentabilidad baja (MER < 2.0)")
        flag_txt = ", ".join(flags) if flags else "sin banderas"
        lines.append(
            f"**Diagnóstico del periodo:** {flag_txt} → "
            f"**{period.diagnosis.recommendation.value}**"
        )
    else:
        lines.append("**Diagnóstico del periodo:** sin días en común (no hay blend posible).")

    if result.meta_only_dates or result.sales_only_dates:
        meta_only = ", ".join(d.isoformat() for d in result.meta_only_dates) or "—"
        sales_only = ", ".join(d.isoformat() for d in result.sales_only_dates) or "—"
        lines.append("")
        lines.append(
            f"**Fechas sin match (excluidas del blend):** "
            f"solo-Meta: {meta_only} · solo-Ventas: {sales_only}"
        )

    return "\n".join(lines)


def _metrics_dict(m: DayMetrics) -> dict:
    return {
        "drop_off_rate": _dec_str(m.drop_off_rate),
        "cost_per_conversation_cop": _dec_str(m.cost_per_conversation_cop),
        "mer": _dec_str(m.mer),
        "global_cpa_cop": _dec_str(m.global_cpa_cop),
        "global_win_rate": _dec_str(m.global_win_rate),
    }


def _diagnosis_dict(d: Diagnosis) -> dict:
    return {
        "high_friction": d.high_friction,
        "poor_profitability": d.poor_profitability,
        "recommendation": d.recommendation.value,
    }


def to_dict(result: MergeResult) -> dict:
    """Exact, JSON-able view (Decimals as strings to preserve precision)."""
    days = [
        {
            "date": d.date.isoformat(),
            "spend_cop": d.spend_cop,
            "inline_link_clicks": d.inline_link_clicks,
            "messaging_conversations_started": d.messaging_conversations_started,
            "total_orders": d.total_orders,
            "total_revenue_cop": d.total_revenue_cop,
            "metrics": _metrics_dict(d.metrics),
            "diagnosis": _diagnosis_dict(d.diagnosis),
        }
        for d in result.days
    ]
    period = _period(result.days)
    period_dict = None
    if period:
        period_dict = {
            "spend_cop": period.spend_cop,
            "inline_link_clicks": period.inline_link_clicks,
            "messaging_conversations_started": period.conversations,
            "total_orders": period.orders,
            "total_revenue_cop": period.revenue_cop,
            "metrics": _metrics_dict(period.metrics),
            "diagnosis": _diagnosis_dict(period.diagnosis),
        }
    return {
        "days": days,
        "period": period_dict,
        "unmatched": {
            "meta_only": [d.isoformat() for d in result.meta_only_dates],
            "sales_only": [d.isoformat() for d in result.sales_only_dates],
        },
    }
