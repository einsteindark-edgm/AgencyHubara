"""Temporal Cloud: daily usage of the account × Temporal's published price."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from src.plugins.costs.domain.money import fmt_usd
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.temporal import TemporalPrices, TemporalProvider

PRICES = TemporalPrices(
    usd_per_million_actions=Decimal("50"),
    usd_per_active_gbh=Decimal("0.042"),
    usd_per_retained_gbh=Decimal("0.00105"),
    plan_fee_ratio=Decimal("0.10"),
)
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
GBH = 3600 * 10**9  # byte-seconds in one GB-hour (Temporal uses GB = 10^9 bytes)


def _day(day: str, ns: str, actions: int, active_gbh: float, retained_gbh: float,
         incomplete: bool = False) -> dict:
    return {
        "startTime": f"{day}T00:00:00Z",
        "endTime": f"{day}T23:59:59Z",
        "incomplete": incomplete,
        "recordGroups": [{
            "groupBys": [{"key": "GROUP_BY_KEY_NAMESPACE", "value": ns}],
            "records": [
                {"type": "RECORD_TYPE_ACTIONS", "unit": "RECORD_UNIT_NUMBER", "value": actions},
                {"type": "RECORD_TYPE_ACTIVE_STORAGE", "unit": "RECORD_UNIT_BYTE_SECONDS",
                 "value": int(active_gbh * GBH)},
                {"type": "RECORD_TYPE_RETAINED_STORAGE", "unit": "RECORD_UNIT_BYTE_SECONDS",
                 "value": int(retained_gbh * GBH)},
            ],
        }],
    }


def _run(provider, rng, pages: list[dict]):
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=pages[len(seen) - 1])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as client:
            return await provider.fetch(client, rng, NOW)

    return asyncio.run(go()), seen


def _provider(**kw) -> TemporalProvider:
    base = dict(api_key="k", namespace="hubara.ri1ti", prices=PRICES)
    base.update(kw)
    return TemporalProvider(**base)


def test_month_is_priced_like_temporals_invoice() -> None:
    # 100k actions = US$5; 5 GB-h active = US$0.21; 500 GB-h retained =
    # US$0.525; plan fee 10 % of US$5.735 = US$0.5735 → US$6.3085.
    page = {"summaries": [_day("2026-10-01", "hubara.ri1ti", 100_000, 5.0, 500.0)],
            "nextPageToken": ""}
    rng = DateRange(date(2026, 10, 1), date(2026, 10, 1))

    report, _ = _run(_provider(), rng, [page])

    assert report.status is Status.OK
    assert report.source is Source.COMPUTED
    total = sum(ln.amount_micros for ln in report.lines)
    assert fmt_usd(total) == "US$6.3085"


def test_lines_are_per_meter_with_their_quantities() -> None:
    page = {"summaries": [_day("2026-11-02", "hubara.ri1ti", 20_000, 1.0, 10.0)],
            "nextPageToken": ""}
    rng = DateRange(date(2026, 11, 2), date(2026, 11, 2))

    report, _ = _run(_provider(), rng, [page])

    by = {ln.key: ln for ln in report.lines}
    assert by["hubara.ri1ti:actions"].amount_micros == 1_000_000
    assert by["hubara.ri1ti:actions"].quantity == 20_000
    assert by["hubara.ri1ti:active"].amount_micros == 42_000
    assert by["hubara.ri1ti:retained"].amount_micros == 10_500
    assert by["hubara.ri1ti:plan_fee"].amount_micros == 105_250
    assert all(ln.currency == "USD" and ln.production for ln in report.lines)


def test_other_namespaces_are_not_production() -> None:
    page = {"summaries": [_day("2026-11-02", "acktos.ri1ti", 1_000_000, 0, 0)],
            "nextPageToken": ""}
    rng = DateRange(date(2026, 11, 2), date(2026, 11, 2))

    report, _ = _run(_provider(), rng, [page])

    assert report.lines and not any(ln.production for ln in report.lines)


def test_request_uses_utc_midnights_and_follows_pages() -> None:
    pages = [
        {"summaries": [_day("2026-11-02", "hubara.ri1ti", 10, 0, 0)], "nextPageToken": "p2"},
        {"summaries": [_day("2026-11-03", "hubara.ri1ti", 10, 0, 0)], "nextPageToken": ""},
    ]
    rng = DateRange(date(2026, 11, 2), date(2026, 11, 3))

    report, seen = _run(_provider(), rng, pages)

    assert seen[0].url.params["start_time_inclusive"] == "2026-11-02T00:00:00Z"
    assert seen[0].url.params["end_time_exclusive"] == "2026-11-04T00:00:00Z"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert seen[1].url.params["page_token"] == "p2"
    actions = {ln.key: ln for ln in report.lines}["hubara.ri1ti:actions"]
    assert actions.quantity == 20


def test_today_is_asked_only_up_to_the_next_utc_midnight() -> None:
    rng = DateRange(date(2026, 11, 3), date(2026, 11, 4))
    page = {"summaries": [_day("2026-11-04", "hubara.ri1ti", 10, 0, 0, incomplete=True)],
            "nextPageToken": ""}

    report, seen = _run(_provider(), rng, [page])

    assert seen[0].url.params["end_time_exclusive"] == "2026-11-05T00:00:00Z"
    assert any("todavía" in n for n in report.notes)


def test_range_older_than_ninety_days_is_trimmed_and_said() -> None:
    rng = DateRange(date(2026, 8, 1), date(2026, 8, 10))
    page = {"summaries": [], "nextPageToken": ""}

    report, seen = _run(_provider(), rng, [page])

    assert seen[0].url.params["start_time_inclusive"] >= "2026-08-06T00:00:00Z"
    assert any("90 días" in w for w in report.warnings)


def test_without_api_key_it_is_not_connected() -> None:
    rng = DateRange(date(2026, 11, 2), date(2026, 11, 2))
    report, seen = _run(_provider(api_key=None), rng, [])

    assert report.status is Status.NOT_CONNECTED
    assert seen == []
