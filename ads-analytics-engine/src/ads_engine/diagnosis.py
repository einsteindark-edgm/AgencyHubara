"""Deterministic diagnosis: thresholds + a fixed decision table.

The LLM analyst *narrates* the interpretation, but the boolean flags and the
recommendation are produced here so two runs over the same numbers always agree.
"""

from __future__ import annotations

from decimal import Decimal

from .models import DayMetrics, Diagnosis, Recommendation

# Thresholds from the spec.
HIGH_FRICTION_DROP_OFF = Decimal("0.40")  # drop-off above this = high funnel friction
MIN_HEALTHY_MER = Decimal("2.0")  # MER below this = poor profitability


def diagnose(metrics: DayMetrics) -> Diagnosis:
    # If the two load-bearing metrics are undefined, we cannot judge — say so.
    if metrics.mer is None or metrics.drop_off_rate is None:
        return Diagnosis(
            high_friction=False,
            poor_profitability=False,
            recommendation=Recommendation.INSUFFICIENT_DATA,
        )

    high_friction = metrics.drop_off_rate > HIGH_FRICTION_DROP_OFF
    poor = metrics.mer < MIN_HEALTHY_MER

    if poor and high_friction:
        # Leaky AND unprofitable → the funnel is the bottleneck: rotate creative.
        rec = Recommendation.ROTATE_CREATIVE
    elif poor:
        # Unprofitable but the funnel converts → look at targeting/pricing.
        rec = Recommendation.REVIEW_TARGETING_OR_PRICING
    else:
        # Profitable (MER >= 2). Scaling makes money; high_friction stays flagged
        # for the analyst to note as a secondary optimization.
        rec = Recommendation.SCALE_BUDGET

    return Diagnosis(high_friction=high_friction, poor_profitability=poor, recommendation=rec)
