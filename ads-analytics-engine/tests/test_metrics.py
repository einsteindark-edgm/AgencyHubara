"""Golden tests: exact metric values, hand-computed independently of the engine.

This file IS the anti-hallucination guarantee referenced in the spec's
Definition of Done. Numbers here were computed by hand, not by the code.
"""

from datetime import date
from decimal import Decimal

import pytest

from ads_engine import metrics
from ads_engine.models import MetaDailyInsight


@pytest.mark.golden
def test_drop_off_rate_exact():
    # 1 - 100/500 = 0.8 ; 1 - 300/400 = 0.25
    assert metrics.drop_off_rate(500, 100) == Decimal("0.8")
    assert metrics.drop_off_rate(400, 300) == Decimal("0.25")


@pytest.mark.golden
def test_cost_per_conversation_exact():
    assert metrics.cost_per_conversation_cop(300000, 100) == Decimal("3000")


@pytest.mark.golden
def test_cost_per_conversation_repeating_decimal_is_exact_then_rounded():
    value = metrics.cost_per_conversation_cop(200000, 300)  # 666.666...
    assert value.quantize(Decimal("0.01")) == Decimal("666.67")


@pytest.mark.golden
def test_mer_exact():
    assert metrics.mer(450000, 300000) == Decimal("1.5")
    # zero revenue over real spend is a real 0, NOT "undefined".
    assert metrics.mer(0, 100000) == Decimal("0")


@pytest.mark.golden
def test_global_cpa_exact():
    assert metrics.global_cpa_cop(300000, 15) == Decimal("20000")


@pytest.mark.golden
def test_global_win_rate_exact():
    assert metrics.global_win_rate(15, 100) == Decimal("0.15")
    assert metrics.global_win_rate(23, 400) == Decimal("0.0575")


@pytest.mark.golden
@pytest.mark.parametrize(
    "fn,args",
    [
        (metrics.drop_off_rate, (0, 0)),  # no clicks
        (metrics.cost_per_conversation_cop, (300000, 0)),  # no conversations
        (metrics.mer, (450000, 0)),  # no spend
        (metrics.global_cpa_cop, (300000, 0)),  # no orders
        (metrics.global_win_rate, (15, 0)),  # no conversations
    ],
)
def test_zero_denominator_returns_none_never_a_number(fn, args):
    assert fn(*args) is None


def test_currency_guard_rejects_non_cop():
    with pytest.raises(ValueError):
        MetaDailyInsight(
            date=date(2026, 6, 1),
            spend_cop=1,
            inline_link_clicks=1,
            messaging_conversations_started=1,
            currency="USD",
        )


def test_negative_values_rejected():
    with pytest.raises(ValueError):
        MetaDailyInsight(
            date=date(2026, 6, 1),
            spend_cop=-1,
            inline_link_clicks=0,
            messaging_conversations_started=0,
        )
