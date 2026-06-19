"""Tests for the date join: inner join + surfacing of unmatched dates."""

from datetime import date

import pytest

from ads_engine.merge import merge
from ads_engine.models import ManualSale, MetaDailyInsight


def _insight(day, spend=1000, clicks=10, conv=5):
    return MetaDailyInsight(
        date=day,
        spend_cop=spend,
        inline_link_clicks=clicks,
        messaging_conversations_started=conv,
    )


def _sale(day, orders=1, revenue=1000):
    return ManualSale(date=day, total_orders=orders, total_revenue_cop=revenue)


def test_inner_join_keeps_only_common_dates_and_surfaces_the_rest():
    insights = [_insight(date(2026, 6, 1)), _insight(date(2026, 6, 2))]
    sales = [_sale(date(2026, 6, 2)), _sale(date(2026, 6, 3))]
    result = merge(insights, sales)
    assert [d.date for d in result.days] == [date(2026, 6, 2)]
    assert result.meta_only_dates == [date(2026, 6, 1)]  # not silently dropped
    assert result.sales_only_dates == [date(2026, 6, 3)]


def test_duplicate_date_raises_loudly():
    with pytest.raises(ValueError):
        merge([_insight(date(2026, 6, 1)), _insight(date(2026, 6, 1))], [])


def test_days_are_sorted_by_date():
    days = [date(2026, 6, 3), date(2026, 6, 1), date(2026, 6, 2)]
    result = merge([_insight(d) for d in days], [_sale(d) for d in days])
    assert [d.date for d in result.days] == sorted(days)
