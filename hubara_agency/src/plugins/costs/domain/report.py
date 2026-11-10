"""What a provider card shows (pure).

A provider adapter returns :class:`CostLine` s in the provider's own currency,
split by Bogotá day. :meth:`ProviderReport.priced` converts each day with that
day's rate and adds the totals. Two rules the tab relies on:

* A provider that is not connected or failed has **no** total (``None``),
  never ``0`` — a zero would read as "it cost nothing".
* Only lines marked ``production`` count in the production total; the rest
  (lab keys, local simulations) are shown apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum

from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.period import DateRange


class Status(str, Enum):
    OK = "ok"
    NOT_CONNECTED = "not_connected"
    ERROR = "error"


class Source(str, Enum):
    #: The provider's invoice/billing data.
    BILLED = "billed"
    #: The provider's own usage meter, in money (DeepSeek, Meta, OpenRouter).
    METERED = "metered"
    #: The provider's usage quantities × its published price (Temporal).
    COMPUTED = "computed"
    #: What each conversation recorded when the call was made (DeepSeek, Jev, Gemini).
    CONVERSATIONS = "conversations"


@dataclass(frozen=True)
class CostLine:
    key: str
    label: str
    currency: str
    #: Native-currency micros by Bogotá day.
    daily: dict[date, int]
    production: bool = True
    #: Heading the line belongs to ("Llave", "Categoría", ...).
    group: str | None = None
    quantity: float | None = None
    unit: str | None = None
    #: Native micros the provider gives for the whole range, not per day
    #: (e.g. an ad campaign's spend). Negative = a deduction.
    undated: int = 0
    # Filled by ``ProviderReport.priced``:
    usd_micros: int | None = None
    cop_micros: int | None = None
    daily_usd: dict[date, int] | None = None

    @property
    def amount_micros(self) -> int:
        return sum(self.daily.values()) + self.undated

    def priced(self, fx: FxTable, start: date, end: date) -> CostLine:
        daily_usd: dict[date, int] = {}
        cop: int | None = 0
        if self.undated:
            usd = fx.usd_micros_over(self.undated, self.currency, start, end)
            if usd is None:
                return replace(self, usd_micros=None, cop_micros=None, daily_usd=None)
            cop = (
                self.undated
                if self.currency.upper() == "COP"
                else fx.cop_micros_over(usd, start, end)
            )
            undated_usd = usd
        else:
            undated_usd = 0
        for day, micros in self.daily.items():
            usd = fx.usd_micros(micros, self.currency, day)
            if usd is None:
                return replace(self, usd_micros=None, cop_micros=None, daily_usd=None)
            daily_usd[day] = usd
            if self.currency.upper() == "COP":
                cop += micros
            else:
                day_cop = fx.cop_micros(usd, day)
                cop = None if (cop is None or day_cop is None) else cop + day_cop
        return replace(
            self,
            usd_micros=sum(daily_usd.values()) + undated_usd,
            cop_micros=cop,
            daily_usd=daily_usd,
        )

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "currency": self.currency,
            "amount_micros": self.amount_micros,
            "usd_micros": self.usd_micros,
            "cop_micros": self.cop_micros,
            "production": self.production,
            "quantity": self.quantity,
            "unit": self.unit,
        }


def _sum_or_none(values: list[int | None]) -> int | None:
    return None if any(v is None for v in values) else sum(v for v in values if v is not None)


@dataclass(frozen=True)
class ProviderReport:
    provider: str
    start: date
    end: date
    status: Status
    source: Source | None = None
    lines: tuple[CostLine, ...] = ()
    #: Plain-Spanish facts about how the number was obtained.
    notes: tuple[str, ...] = ()
    #: Plain-Spanish problems with part of the number.
    warnings: tuple[str, ...] = ()
    error: str | None = None
    how_to_connect: str | None = None
    fx_note: str | None = None
    production_usd_micros: int | None = None
    production_cop_micros: int | None = None
    other_usd_micros: int | None = None
    daily_production_usd: tuple[tuple[date, int], ...] = field(default_factory=tuple)

    # ── constructors ──────────────────────────────────────────────────────
    @classmethod
    def ok(
        cls,
        provider: str,
        rng: DateRange,
        source: Source,
        lines: list[CostLine],
        *,
        notes: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> ProviderReport:
        return cls(
            provider=provider,
            start=rng.start,
            end=rng.end,
            status=Status.OK,
            source=source,
            lines=tuple(lines),
            notes=tuple(notes or ()),
            warnings=tuple(warnings or ()),
        )

    @classmethod
    def not_connected(
        cls,
        provider: str,
        rng: DateRange,
        *,
        how_to_connect: str,
        notes: list[str] | None = None,
    ) -> ProviderReport:
        return cls(
            provider=provider,
            start=rng.start,
            end=rng.end,
            status=Status.NOT_CONNECTED,
            how_to_connect=how_to_connect,
            notes=tuple(notes or ()),
        )

    @classmethod
    def failed(cls, provider: str, rng: DateRange, error: str) -> ProviderReport:
        return cls(
            provider=provider, start=rng.start, end=rng.end, status=Status.ERROR, error=error
        )

    # ── pricing ───────────────────────────────────────────────────────────
    def priced(self, fx: FxTable) -> ProviderReport:
        if self.status is not Status.OK:
            return replace(self, fx_note=fx.note or None)
        lines = tuple(line.priced(fx, self.start, self.end) for line in self.lines)
        warnings = list(self.warnings)
        missing = sorted({ln.currency for ln in lines if ln.usd_micros is None})
        for cur in missing:
            warnings.append(
                f"No hay tasa de cambio para {cur}: esas líneas no se suman al total."
            )
        prod = [ln for ln in lines if ln.production]
        other = [ln for ln in lines if not ln.production]
        daily: dict[date, int] = {}
        for ln in prod:
            for day, usd in (ln.daily_usd or {}).items():
                daily[day] = daily.get(day, 0) + usd
        days = sorted(set(daily) | {d for d in _days(self.start, self.end)})
        return replace(
            self,
            lines=lines,
            warnings=tuple(warnings),
            fx_note=fx.note or None,
            production_usd_micros=_sum_or_none([ln.usd_micros for ln in prod]),
            production_cop_micros=_sum_or_none([ln.cop_micros for ln in prod]),
            other_usd_micros=(
                _sum_or_none([ln.usd_micros for ln in other]) if other else None
            ),
            daily_production_usd=tuple((d, daily.get(d, 0)) for d in days),
        )

    def to_json(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "source": self.source.value if self.source else None,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "production_usd_micros": self.production_usd_micros,
            "production_cop_micros": self.production_cop_micros,
            "other_usd_micros": self.other_usd_micros,
            "daily_production_usd_micros": [
                {"date": d.isoformat(), "usd_micros": v} for d, v in self.daily_production_usd
            ],
            "lines": [ln.to_json() for ln in self.lines],
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "error": self.error,
            "how_to_connect": self.how_to_connect,
            "fx_note": self.fx_note,
        }


def _days(start: date, end: date) -> list[date]:
    return list(DateRange(start, end).iter_days())
