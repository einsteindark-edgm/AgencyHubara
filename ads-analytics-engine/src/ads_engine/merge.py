"""Join Meta insights with manual sales by date.

Inner join (only days present in BOTH feeds get blended metrics), but the days
that appear in only one feed are *surfaced*, not silently dropped — silent
truncation reads as "covered everything" when it didn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .diagnosis import diagnose
from .metrics import compute_metrics
from .models import BlendedDay, ManualSale, MetaDailyInsight


@dataclass(frozen=True)
class MergeResult:
    days: list[BlendedDay]
    meta_only_dates: list[date]
    sales_only_dates: list[date]


def build_blended_day(
    day: date,
    *,
    spend_cop: int,
    inline_link_clicks: int,
    conversations_started: int,
    total_orders: int,
    total_revenue_cop: int,
) -> BlendedDay:
    """Single source of truth for turning raw joined facts into a BlendedDay.

    Used both by ``merge()`` and when rehydrating rows from the store, so the
    math is identical on the compute path and the report path.
    """
    metrics = compute_metrics(
        spend_cop=spend_cop,
        inline_link_clicks=inline_link_clicks,
        conversations_started=conversations_started,
        total_orders=total_orders,
        total_revenue_cop=total_revenue_cop,
    )
    return BlendedDay(
        date=day,
        spend_cop=spend_cop,
        inline_link_clicks=inline_link_clicks,
        messaging_conversations_started=conversations_started,
        total_orders=total_orders,
        total_revenue_cop=total_revenue_cop,
        metrics=metrics,
        diagnosis=diagnose(metrics),
    )


def _index_by_date(rows: list, kind: str) -> dict:
    out: dict = {}
    for row in rows:
        if row.date in out:
            raise ValueError(f"duplicate {kind} row for date {row.date.isoformat()}")
        out[row.date] = row
    return out


def merge(insights: list[MetaDailyInsight], sales: list[ManualSale]) -> MergeResult:
    meta = _index_by_date(insights, "meta insight")
    sale = _index_by_date(sales, "manual sale")

    common = sorted(set(meta) & set(sale))
    days = [
        build_blended_day(
            d,
            spend_cop=meta[d].spend_cop,
            inline_link_clicks=meta[d].inline_link_clicks,
            conversations_started=meta[d].messaging_conversations_started,
            total_orders=sale[d].total_orders,
            total_revenue_cop=sale[d].total_revenue_cop,
        )
        for d in common
    ]
    return MergeResult(
        days=days,
        meta_only_dates=sorted(set(meta) - set(sale)),
        sales_only_dates=sorted(set(sale) - set(meta)),
    )
