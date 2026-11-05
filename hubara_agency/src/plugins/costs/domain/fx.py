"""Exchange rates used to put every provider in USD and COP (pure).

COP uses the official TRM of each day (Superintendencia Financiera, published
on datos.gov.co); weekends and holidays carry the last published rate. Other
currencies (DeepSeek also bills in CNY) use a configured rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class FxTable:
    #: COP per 1 USD, by Bogotá day.
    cop_per_usd: dict[date, Decimal]
    #: Units of each other currency per 1 USD (e.g. ``{"CNY": Decimal("7.10")}``).
    per_usd: dict[str, Decimal] = field(default_factory=dict)
    #: Where the rates come from, in plain Spanish (shown under the total).
    note: str = ""

    def cop_rate(self, day: date) -> Decimal | None:
        """TRM of ``day``: the last one published on or before it (else the first after)."""
        if not self.cop_per_usd:
            return None
        before = [d for d in self.cop_per_usd if d <= day]
        pick = max(before) if before else min(self.cop_per_usd)
        return self.cop_per_usd[pick]

    def usd_micros(self, amount_micros: int, currency: str, day: date) -> int | None:
        cur = currency.upper()
        if cur == "USD":
            return amount_micros
        rate = self.cop_rate(day) if cur == "COP" else self.per_usd.get(cur)
        if not rate:
            return None
        return _round(Decimal(amount_micros) / rate)

    def cop_micros(self, usd_micros: int, day: date) -> int | None:
        rate = self.cop_rate(day)
        return None if rate is None else _round(Decimal(usd_micros) * rate)


def _round(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_EVEN))
