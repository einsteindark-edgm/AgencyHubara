"""The date range the operator picks in the Costs tab (Bogotá calendar days)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.plugins.costs.domain.period import DateRange, InvalidDateRange

TODAY = date(2026, 11, 4)


def test_range_covers_bogota_days_as_utc_instants() -> None:
    rng = DateRange.parse("2026-10-22", "2026-11-04", today=TODAY)

    assert rng.days == 14
    # Bogotá is UTC-5 all year: its midnight is 05:00Z.
    assert rng.utc_start == datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    assert rng.utc_end_exclusive == datetime(2026, 11, 5, 5, 0, tzinfo=timezone.utc)


def test_default_range_is_the_last_two_weeks_including_today() -> None:
    rng = DateRange.last_days(14, today=TODAY)

    assert (rng.start, rng.end) == (date(2026, 10, 22), date(2026, 11, 4))


def test_start_after_end_is_rejected_in_plain_spanish() -> None:
    with pytest.raises(InvalidDateRange, match="inicio"):
        DateRange.parse("2026-11-04", "2026-11-01", today=TODAY)


def test_future_end_is_rejected() -> None:
    with pytest.raises(InvalidDateRange, match="futur"):
        DateRange.parse("2026-11-01", "2026-11-05", today=TODAY)


def test_too_long_range_is_rejected_so_queries_never_hang() -> None:
    with pytest.raises(InvalidDateRange, match="92"):
        DateRange.parse("2026-07-01", "2026-11-04", today=TODAY)


def test_malformed_date_is_rejected() -> None:
    with pytest.raises(InvalidDateRange, match="AAAA-MM-DD"):
        DateRange.parse("04/11/2026", "2026-11-04", today=TODAY)


def test_iter_days_lists_every_calendar_day() -> None:
    rng = DateRange.parse("2026-10-30", "2026-11-02", today=TODAY)

    assert [d.isoformat() for d in rng.iter_days()] == [
        "2026-10-30",
        "2026-10-31",
        "2026-11-01",
        "2026-11-02",
    ]
