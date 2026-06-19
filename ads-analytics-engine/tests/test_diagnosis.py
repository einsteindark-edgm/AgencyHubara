"""Golden tests for the deterministic decision table."""

import pytest

from ads_engine.diagnosis import diagnose
from ads_engine.metrics import compute_metrics
from ads_engine.models import Recommendation


def _diag(**kw):
    return diagnose(compute_metrics(**kw))


@pytest.mark.golden
def test_healthy_scales():
    d = _diag(
        spend_cop=100000,
        inline_link_clicks=100,
        conversations_started=80,
        total_orders=40,
        total_revenue_cop=300000,
    )  # drop=0.2, mer=3.0
    assert d.high_friction is False
    assert d.poor_profitability is False
    assert d.recommendation is Recommendation.SCALE_BUDGET


@pytest.mark.golden
def test_poor_and_friction_rotates_creative():
    d = _diag(
        spend_cop=300000,
        inline_link_clicks=500,
        conversations_started=100,
        total_orders=15,
        total_revenue_cop=450000,
    )  # drop=0.8, mer=1.5
    assert d.high_friction is True
    assert d.poor_profitability is True
    assert d.recommendation is Recommendation.ROTATE_CREATIVE


@pytest.mark.golden
def test_poor_without_friction_reviews_targeting():
    d = _diag(
        spend_cop=100000,
        inline_link_clicks=100,
        conversations_started=90,
        total_orders=5,
        total_revenue_cop=100000,
    )  # drop=0.1, mer=1.0
    assert d.high_friction is False
    assert d.poor_profitability is True
    assert d.recommendation is Recommendation.REVIEW_TARGETING_OR_PRICING


@pytest.mark.golden
def test_profitable_with_friction_still_scales_but_flags_friction():
    d = _diag(
        spend_cop=100000,
        inline_link_clicks=100,
        conversations_started=30,
        total_orders=20,
        total_revenue_cop=300000,
    )  # drop=0.7, mer=3.0
    assert d.high_friction is True
    assert d.poor_profitability is False
    assert d.recommendation is Recommendation.SCALE_BUDGET


@pytest.mark.golden
def test_insufficient_data_when_no_clicks():
    d = _diag(
        spend_cop=100000,
        inline_link_clicks=0,
        conversations_started=0,
        total_orders=0,
        total_revenue_cop=0,
    )
    assert d.recommendation is Recommendation.INSUFFICIENT_DATA


@pytest.mark.golden
def test_threshold_boundaries_are_inclusive_healthy():
    # drop-off exactly 0.40 is NOT > 0.40; MER exactly 2.0 is NOT < 2.0 → healthy.
    d = _diag(
        spend_cop=100000,
        inline_link_clicks=100,
        conversations_started=60,
        total_orders=10,
        total_revenue_cop=200000,
    )  # drop=0.40, mer=2.0
    assert d.high_friction is False
    assert d.poor_profitability is False
    assert d.recommendation is Recommendation.SCALE_BUDGET
