"""The official COP/USD rate (TRM) of each day, with a configured fallback."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx

from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.providers.fx import TrmSource

RANGE = DateRange(date(2026, 10, 30), date(2026, 11, 2))
ROWS = [
    {"valor": "3166.91", "unidad": "COP", "vigenciadesde": "2026-10-30T00:00:00.000",
     "vigenciahasta": "2026-10-30T00:00:00.000"},
    {"valor": "3182.99", "unidad": "COP", "vigenciadesde": "2026-10-31T00:00:00.000",
     "vigenciahasta": "2026-11-03T00:00:00.000"},
]


def _load(handler, **kw):
    src = TrmSource(fallback_cop_per_usd=Decimal("3200"), per_usd={"CNY": Decimal("7.1")}, **kw)
    seen: list[httpx.Request] = []

    def h(req):
        seen.append(req)
        return handler(req)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            return await src.load(client, RANGE)

    return asyncio.run(go()), seen


def test_weekend_and_holiday_days_carry_the_published_rate() -> None:
    fx, _ = _load(lambda req: httpx.Response(200, json=ROWS))

    assert fx.cop_rate(date(2026, 10, 30)) == Decimal("3166.91")
    assert fx.cop_rate(date(2026, 11, 1)) == Decimal("3182.99")
    assert fx.cop_rate(date(2026, 11, 2)) == Decimal("3182.99")
    assert fx.per_usd == {"CNY": Decimal("7.1")}
    assert "TRM" in fx.note


def test_query_asks_rates_overlapping_the_range() -> None:
    _, seen = _load(lambda req: httpx.Response(200, json=ROWS))

    where = seen[0].url.params["$where"]
    assert "vigenciahasta >= '2026-10-30T00:00:00'" in where
    assert "vigenciadesde <= '2026-11-02T00:00:00'" in where


def test_when_the_trm_cannot_be_read_the_fallback_rate_is_used_and_said() -> None:
    fx, _ = _load(lambda req: httpx.Response(503))

    assert fx.cop_rate(date(2026, 11, 1)) == Decimal("3200")
    assert "respaldo" in fx.note
