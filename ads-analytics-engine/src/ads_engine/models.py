"""Frozen, JSON-safe contracts for the engine (COP-only MVP).

Stdlib-only: validation lives in ``__post_init__`` so a malformed row fails
loudly at the boundary instead of silently poisoning a metric downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

CURRENCY = "COP"


def _check_non_negative_int(name: str, value: object) -> None:
    # bool is an int subclass; reject it explicitly so True/False can't sneak in.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")


def _check_currency(value: str) -> None:
    if value != CURRENCY:
        raise ValueError(
            f"MVP is {CURRENCY}-only; got currency={value!r}. "
            "Mixing currencies in MER/CPA is the #1 expected bug — refusing to compute. "
            "Dated FX support is a Phase-2 concern."
        )


@dataclass(frozen=True)
class MetaDailyInsight:
    """One day of Meta Ads insights for a CTWA campaign/account."""

    date: date
    spend_cop: int
    inline_link_clicks: int
    messaging_conversations_started: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        _check_currency(self.currency)
        _check_non_negative_int("spend_cop", self.spend_cop)
        _check_non_negative_int("inline_link_clicks", self.inline_link_clicks)
        _check_non_negative_int(
            "messaging_conversations_started", self.messaging_conversations_started
        )


@dataclass(frozen=True)
class ManualSale:
    """One day of manually-recorded WhatsApp sales."""

    date: date
    total_orders: int
    total_revenue_cop: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        _check_currency(self.currency)
        _check_non_negative_int("total_orders", self.total_orders)
        _check_non_negative_int("total_revenue_cop", self.total_revenue_cop)


@dataclass(frozen=True)
class DayMetrics:
    """The five derived metrics. ``None`` means an undefined denominator (never a guessed number)."""

    drop_off_rate: Decimal | None
    cost_per_conversation_cop: Decimal | None
    mer: Decimal | None
    global_cpa_cop: Decimal | None
    global_win_rate: Decimal | None


class Recommendation(str, Enum):
    SCALE_BUDGET = "scale_budget"
    ROTATE_CREATIVE = "rotate_creative"
    REVIEW_TARGETING_OR_PRICING = "review_targeting_or_pricing"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class Diagnosis:
    high_friction: bool
    poor_profitability: bool
    recommendation: Recommendation


@dataclass(frozen=True)
class BlendedDay:
    """A joined day: raw source facts + the metrics/diagnosis derived from them."""

    date: date
    spend_cop: int
    inline_link_clicks: int
    messaging_conversations_started: int
    total_orders: int
    total_revenue_cop: int
    metrics: DayMetrics
    diagnosis: Diagnosis
