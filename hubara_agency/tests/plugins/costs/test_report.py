"""What a provider card shows: lines in their own currency, priced in USD and COP."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.money import fmt_usd, to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import (
    CostLine,
    ProviderReport,
    Source,
    Status,
)

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
FX = FxTable(
    cop_per_usd={date(2026, 11, 2): Decimal("3182.99"), date(2026, 11, 3): Decimal("3182.99")},
    per_usd={"CNY": Decimal("7.10")},
    note="TRM oficial",
)


def _line(**kw) -> CostLine:
    base = dict(key="k", label="L", currency="USD", daily={}, production=True)
    base.update(kw)
    return CostLine(**base)


def test_provider_strings_become_integer_micros() -> None:
    assert to_micros("1.0232362140000000") == 1_023_236
    assert to_micros(Decimal("0.0125")) == 12_500
    assert fmt_usd(7_888_983) == "US$7.8890"


def test_cop_cost_is_converted_with_each_days_trm() -> None:
    line = _line(
        currency="COP",
        daily={date(2026, 11, 2): to_micros("3182.99"), date(2026, 11, 3): to_micros("6365.98")},
    )
    priced = ProviderReport.ok("whatsapp", RANGE, Source.METERED, [line]).priced(FX)

    (out,) = priced.lines
    assert out.amount_micros == to_micros("9548.97")
    assert out.usd_micros == 3_000_000  # US$1 + US$2


def test_usd_cost_also_shows_its_cop_equivalent() -> None:
    line = _line(daily={date(2026, 11, 2): 1_000_000})
    out = ProviderReport.ok("deepseek", RANGE, Source.METERED, [line]).priced(FX)

    assert out.lines[0].cop_micros == to_micros("3182.99")


def test_only_production_lines_count_in_the_production_total() -> None:
    lines = [
        _line(key="prod", daily={date(2026, 11, 2): 7_000_000}),
        _line(key="lab", daily={date(2026, 11, 2): 2_000_000}, production=False),
    ]
    out = ProviderReport.ok("deepseek", RANGE, Source.METERED, lines).priced(FX)

    assert out.production_usd_micros == 7_000_000
    assert out.other_usd_micros == 2_000_000


def test_cny_uses_the_configured_rate() -> None:
    line = _line(currency="CNY", daily={date(2026, 11, 2): to_micros("7.10")})
    out = ProviderReport.ok("deepseek", RANGE, Source.METERED, [line]).priced(FX)

    assert out.lines[0].usd_micros == 1_000_000


def test_unknown_currency_is_never_summed_as_zero() -> None:
    line = _line(currency="EUR", daily={date(2026, 11, 2): 5_000_000})
    out = ProviderReport.ok("gemini", RANGE, Source.BILLED, [line]).priced(FX)

    assert out.lines[0].usd_micros is None
    assert out.production_usd_micros is None
    assert any("EUR" in w for w in out.warnings)


def test_not_connected_provider_has_no_total_instead_of_zero() -> None:
    out = ProviderReport.not_connected("openrouter", RANGE, how_to_connect="Crea la llave").priced(FX)

    assert out.status is Status.NOT_CONNECTED
    assert out.production_usd_micros is None
    assert out.to_json()["production_usd_micros"] is None


def test_json_has_daily_production_series_in_usd() -> None:
    lines = [
        _line(key="a", daily={date(2026, 11, 2): 1_000_000, date(2026, 11, 3): 500_000}),
        _line(key="b", daily={date(2026, 11, 3): 250_000}),
        _line(key="lab", daily={date(2026, 11, 3): 9_000_000}, production=False),
    ]
    payload = ProviderReport.ok("deepseek", RANGE, Source.METERED, lines).priced(FX).to_json()

    assert payload["status"] == "ok"
    assert payload["start"] == "2026-11-02"
    assert payload["daily_production_usd_micros"] == [
        {"date": "2026-11-02", "usd_micros": 1_000_000},
        {"date": "2026-11-03", "usd_micros": 750_000},
    ]
    assert payload["lines"][0]["key"] == "a"


def test_without_non_production_lines_there_is_no_other_total() -> None:
    out = ProviderReport.ok("whatsapp", RANGE, Source.METERED, [_line(daily={})]).priced(FX)

    assert out.other_usd_micros is None
    assert out.production_usd_micros == 0


def test_an_amount_without_days_uses_the_average_trm_of_the_range() -> None:
    fx = FxTable(
        cop_per_usd={date(2026, 11, 2): Decimal("3000"), date(2026, 11, 3): Decimal("5000")},
        note="",
    )
    line = _line(currency="COP", daily={}, undated=to_micros("8000"))
    out = ProviderReport.ok("campaigns", RANGE, Source.METERED, [line]).priced(fx)

    assert out.lines[0].amount_micros == to_micros("8000")
    assert out.lines[0].usd_micros == 2_000_000  # 8000 COP / 4000 average
    assert out.production_usd_micros == 2_000_000
    assert out.daily_production_usd == ((date(2026, 11, 2), 0), (date(2026, 11, 3), 0))


def test_a_deduction_lowers_the_total() -> None:
    lines = [
        _line(key="meta", daily={date(2026, 11, 2): 3_000_000}),
        _line(key="minus", daily={}, undated=-1_000_000),
    ]
    out = ProviderReport.ok("whatsapp", RANGE, Source.METERED, lines).priced(FX)

    assert out.production_usd_micros == 2_000_000
    assert out.to_json()["lines"][1]["usd_micros"] == -1_000_000
