"""Render the blended report — a Markdown table + a JSON-able dict.

Two sections when campaign data is present:
1. **Per campaign** — funnel only (spend, clicks, conversations, drop-off,
   cost/conversation). Meta-side metrics that ARE attributable per campaign.
2. **Account (blended)** — MER, Global CPA, win rate, revenue. These can't be
   split by campaign (manual WhatsApp sales aren't attributable), so they stay
   account-level.

Every number rendered here comes straight from the engine; nothing is re-derived
ad hoc. Period rows aggregate RAW totals first, then compute (the correct blended
way — not an average of ratios).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .diagnosis import diagnose
from .merge import MergeResult
from .meta_mcp import funnels_from_mcp
from .metrics import compute_metrics
from .models import CampaignFunnel, DayMetrics, Diagnosis


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

_CAMPAIGN_HEADER = (
    "| Campaña | Spend | Clicks | Conversaciones | Drop-off | Costo/Conv | Señal |\n"
    "|---|--:|--:|--:|--:|--:|---|"
)


def _row(label, spend, clicks, conv, drop, cpc, orders, mer_v, cpa, win, rec) -> str:
    return (
        f"| {label} | {_fmt_cop(spend)} | {clicks} | {conv} | {_fmt_pct(drop)} | "
        f"{_fmt_cop(cpc)} | {orders} | {_fmt_ratio(mer_v)} | {_fmt_cop(cpa)} | "
        f"{_fmt_pct(win)} | {rec} |"
    )


def _campaign_section(campaigns: list[CampaignFunnel]) -> list[str]:
    lines = ["### Por campaña (embudo — métricas Meta, sin ingreso)", "", _CAMPAIGN_HEADER]
    for c in campaigns:
        lines.append(
            f"| {c.campaign_name} | {_fmt_cop(c.spend_cop)} | {c.inline_link_clicks} | "
            f"{c.messaging_conversations_started} | {_fmt_pct(c.drop_off_rate)} | "
            f"{_fmt_cop(c.cost_per_conversation_cop)} | {c.recommendation.value} |"
        )
    return lines


def render_markdown(result: MergeResult, campaigns: list[CampaignFunnel] | None = None) -> str:
    lines = ["## Hubara — Ads Analytics (CTWA)", ""]

    if campaigns:
        lines += _campaign_section(campaigns)
        lines += ["", "### Cuenta (blended por día)", ""]

    lines.append(_HEADER)
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

    if campaigns:
        lines.append("")
        lines.append(
            "> Ingreso / MER / CPA global / win-rate son SOLO a nivel cuenta: la venta "
            "manual de WhatsApp no se atribuye determinísticamente a una campaña. Por "
            "campaña se mide el embudo (drop-off, costo/conversación) — lo que decide "
            "qué creativo rotar."
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


def _campaign_dict(c: CampaignFunnel) -> dict:
    return {
        "campaign_id": c.campaign_id,
        "campaign_name": c.campaign_name,
        "spend_cop": c.spend_cop,
        "inline_link_clicks": c.inline_link_clicks,
        "messaging_conversations_started": c.messaging_conversations_started,
        "drop_off_rate": _dec_str(c.drop_off_rate),
        "cost_per_conversation_cop": _dec_str(c.cost_per_conversation_cop),
        "high_friction": c.high_friction,
        "recommendation": c.recommendation.value,
    }


def to_dict(result: MergeResult, campaigns: list[CampaignFunnel] | None = None) -> dict:
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
        "campaigns": [_campaign_dict(c) for c in (campaigns or [])],
        "unmatched": {
            "meta_only": [d.isoformat() for d in result.meta_only_dates],
            "sales_only": [d.isoformat() for d in result.sales_only_dates],
        },
    }


def render_mcp_markdown(campaigns: list) -> str:
    """Objective-aware report straight from the official MCP `ads_get_ad_entities`.

    Messaging campaigns get the CTWA funnel; other objectives (sales, traffic) get
    Meta's own result/cost. Revenue/MER stay account-level (manual sales).
    """
    messaging = funnels_from_mcp(campaigns)
    others = sorted(
        (c for c in campaigns if not c.is_messaging),
        key=lambda c: (-c.spend_cop, c.campaign_id),
    )
    total_spend = sum(c.spend_cop for c in campaigns)

    lines = ["## Hubara — Ads (Meta · por campaña)", ""]
    if messaging:
        lines += _campaign_section(messaging)
    if others:
        lines += [
            "",
            "### Otras campañas (por objetivo — resultado/costo según Meta)",
            "",
            "| Campaña | Objetivo | Spend | Clicks | Resultado | Costo/Resultado |",
            "|---|---|--:|--:|--:|--:|",
        ]
        for c in others:
            cpr = "—" if c.cost_per_result_cop is None else _fmt_cop(c.cost_per_result_cop)
            result = f"{c.result_count} {c.result_type}".strip()
            lines.append(
                f"| {c.campaign_name} | {c.objective} | {_fmt_cop(c.spend_cop)} | "
                f"{c.link_clicks} | {result} | {cpr} |"
            )
    lines += [
        "",
        f"**Gasto total de la cuenta (periodo):** {_fmt_cop(total_spend)}",
        "",
        "> Las ventas de WhatsApp se cargan aparte (`ingest-sales`) y NO se atribuyen por "
        'campaña; las "Meta purchases" subcuentan las ventas reales de WhatsApp. '
        "MER / CPA global = nivel cuenta.",
    ]
    return "\n".join(lines)
