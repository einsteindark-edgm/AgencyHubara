"""The service in front of the providers: cache, one query at a time, time limit."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source, Status
from src.plugins.costs.service import CostsService, UnknownProvider

TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
PAST = DateRange(date(2026, 10, 1), date(2026, 10, 14))
LIVE = DateRange(date(2026, 10, 22), TODAY)


class FakeFx:
    def __init__(self) -> None:
        self.calls = 0

    async def load(self, client, rng):
        self.calls += 1
        return FxTable(cop_per_usd={rng.start: Decimal("4000")}, note="TRM fija de prueba")


class FakeProvider:
    id = "fake"
    label = "Fake"

    def __init__(self, *, delay: float = 0.0, boom: Exception | None = None) -> None:
        self.calls = 0
        self.delay = delay
        self.boom = boom

    @property
    def connected(self) -> bool:
        return True

    async def fetch(self, client, rng, now, ctx=None):
        self.calls += 1
        self.ctx = ctx
        await asyncio.sleep(self.delay)
        if self.boom:
            raise self.boom
        line = CostLine(key="k", label="k", currency="USD", daily={rng.start: 1_000_000})
        return ProviderReport.ok(self.id, rng, Source.METERED, [line])


def _service(provider, fx=None, timeout_s=5.0) -> CostsService:
    return CostsService(
        [provider],
        fx or FakeFx(),
        timeout_s=timeout_s,
        live_ttl_s=600,
        closed_ttl_s=3600,
        now=lambda: NOW,
    )


def test_report_is_priced_and_says_when_it_was_fetched() -> None:
    served = asyncio.run(_service(FakeProvider()).report("fake", PAST))

    assert served.report.status is Status.OK
    assert served.report.production_usd_micros == 1_000_000
    assert served.report.production_cop_micros == 4_000_000_000
    assert served.fetched_at == NOW
    assert served.cached is False


def test_second_ask_for_the_same_range_does_not_call_the_provider_again() -> None:
    provider = FakeProvider()
    svc = _service(provider)

    async def go():
        await svc.report("fake", PAST)
        return await svc.report("fake", PAST)

    served = asyncio.run(go())
    assert provider.calls == 1
    assert served.cached is True


def test_refresh_skips_the_cache() -> None:
    provider = FakeProvider()
    svc = _service(provider)

    async def go():
        await svc.report("fake", LIVE)
        await svc.report("fake", LIVE, refresh=True)

    asyncio.run(go())
    assert provider.calls == 2


def test_concurrent_asks_share_one_provider_call() -> None:
    provider = FakeProvider(delay=0.05)
    svc = _service(provider)

    async def go():
        return await asyncio.gather(*(svc.report("fake", PAST) for _ in range(5)))

    asyncio.run(go())
    assert provider.calls == 1


def test_slow_provider_becomes_an_error_card_instead_of_hanging() -> None:
    served = asyncio.run(_service(FakeProvider(delay=1.0), timeout_s=0.05).report("fake", PAST))

    assert served.report.status is Status.ERROR
    assert "no respondió" in (served.report.error or "")


def test_unexpected_bug_in_an_adapter_is_an_error_card_not_a_500() -> None:
    served = asyncio.run(_service(FakeProvider(boom=KeyError("data"))).report("fake", PAST))

    assert served.report.status is Status.ERROR
    assert served.report.production_usd_micros is None


def test_errors_are_not_cached() -> None:
    provider = FakeProvider(boom=RuntimeError("x"))
    svc = _service(provider)

    async def go():
        await svc.report("fake", PAST)
        await svc.report("fake", PAST)

    asyncio.run(go())
    assert provider.calls == 2


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(UnknownProvider):
        asyncio.run(_service(FakeProvider()).report("nope", PAST))


def test_catalog_lists_providers_and_whether_they_are_connected() -> None:
    assert _service(FakeProvider()).catalog() == [
        {"id": "fake", "label": "Fake", "connected": True}
    ]


def test_the_request_context_reaches_the_card() -> None:
    from src.plugins.costs.providers.base import FetchContext

    provider = FakeProvider()
    ctx = FetchContext(cast_get=None)
    asyncio.run(_service(provider).report("fake", PAST, ctx=ctx))

    assert provider.ctx is ctx
