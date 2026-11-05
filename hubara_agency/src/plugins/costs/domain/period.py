"""The date range of the Costs tab — Bogotá calendar days (pure, no I/O).

Every provider is asked for the same instants: Bogotá midnight of ``start``
up to (excluding) Bogotá midnight of the day after ``end``. Colombia has no
daylight saving time, so its midnight is always 05:00 UTC.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

#: Bogotá is UTC-5 all year round.
BOGOTA = timezone(timedelta(hours=-5), "America/Bogota")

#: Longest range we accept. Temporal Cloud only keeps 90 days of usage and a
#: bounded range is what keeps every query fast (the operator asked that the
#: tab never hangs).
MAX_DAYS = 92


class InvalidDateRange(ValueError):
    """The operator asked for a range we refuse to query (message in Spanish)."""


def _parse_day(raw: str, which: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except (AttributeError, ValueError):
        raise InvalidDateRange(
            f"La fecha {which} debe tener el formato AAAA-MM-DD (llegó {raw!r})."
        ) from None


@dataclass(frozen=True)
class DateRange:
    """Inclusive range of Bogotá calendar days."""

    start: date
    end: date

    @classmethod
    def parse(cls, start: str, end: str, *, today: date) -> DateRange:
        rng = cls(_parse_day(start, "de inicio"), _parse_day(end, "final"))
        if rng.start > rng.end:
            raise InvalidDateRange("La fecha de inicio es posterior a la fecha final.")
        if rng.end > today:
            raise InvalidDateRange("La fecha final no puede ser futura.")
        if rng.days > MAX_DAYS:
            raise InvalidDateRange(
                f"El rango no puede pasar de {MAX_DAYS} días (pediste {rng.days})."
            )
        return rng

    @classmethod
    def last_days(cls, n: int, *, today: date) -> DateRange:
        return cls(today - timedelta(days=n - 1), today)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def utc_start(self) -> datetime:
        return datetime.combine(self.start, time(0), BOGOTA).astimezone(timezone.utc)

    @property
    def utc_end_exclusive(self) -> datetime:
        day_after = self.end + timedelta(days=1)
        return datetime.combine(day_after, time(0), BOGOTA).astimezone(timezone.utc)

    def iter_days(self) -> Iterator[date]:
        for offset in range(self.days):
            yield self.start + timedelta(days=offset)
