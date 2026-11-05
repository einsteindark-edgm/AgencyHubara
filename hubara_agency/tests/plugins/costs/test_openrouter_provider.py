"""OpenRouter (Jev): spend by API key and day from the analytics API."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.openrouter import OpenRouterProvider

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)

ANALYTICS = {"data": {
    "data": [
        {"date__day": "2026-11-02T00:00:00.000Z", "api_key_id": "jev-prod",
         "total_usage": 0.125, "request_count": "40"},
        {"date__day": "2026-11-03T00:00:00.000Z", "api_key_id": "jev-prod",
         "total_usage": "0.075", "request_count": 25},
        {"date__day": "2026-11-03T00:00:00.000Z", "api_key_id": "pruebas",
         "total_usage": 0.5, "request_count": 3},
    ],
    "metadata": {"query_time_ms": 12, "row_count": 3, "truncated": False},
}}
KEY = {"data": {"usage": 1.77, "usage_daily": 0.0035, "usage_weekly": 0.26,
                "usage_monthly": 0.29, "is_management_key": False}}


def _run(provider: OpenRouterProvider, handler):
    seen: list[httpx.Request] = []

    def _h(req):
        seen.append(req)
        return handler(req)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as client:
            return await provider.fetch(client, RANGE, NOW)

    return asyncio.run(go()), seen


def _ok(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/analytics/query"):
        return httpx.Response(200, json=ANALYTICS)
    if req.url.path.endswith("/key"):
        return httpx.Response(200, json=KEY)
    return httpx.Response(404)


def test_spend_is_split_by_key_and_day() -> None:
    report, _ = _run(OpenRouterProvider(management_key="m", api_key="k", production_keys=[]), _ok)

    assert report.status is Status.OK
    assert report.source is Source.METERED
    by = {ln.label: ln for ln in report.lines}
    assert by["jev-prod"].daily == {
        date(2026, 11, 2): to_micros("0.125"),
        date(2026, 11, 3): to_micros("0.075"),
    }
    assert by["jev-prod"].quantity == 65
    # No production list configured → every key counts as production.
    assert all(ln.production for ln in report.lines)


def test_configured_production_keys_leave_the_rest_apart() -> None:
    report, _ = _run(
        OpenRouterProvider(management_key="m", api_key="k", production_keys=["jev-prod"]), _ok
    )

    by = {ln.label: ln for ln in report.lines}
    assert by["jev-prod"].production is True
    assert by["pruebas"].production is False


def test_query_asks_the_bogota_range_with_seconds_precision() -> None:
    _, seen = _run(OpenRouterProvider(management_key="m", api_key="k", production_keys=[]), _ok)

    query = next(r for r in seen if r.url.path.endswith("/analytics/query"))
    body = json.loads(query.content)
    assert body["time_range"] == {"start": "2026-11-02T05:00:00Z", "end": "2026-11-04T05:00:00Z"}
    assert body["dimensions"] == ["api_key_id"]
    assert body["granularity"] == "day"
    assert "total_usage" in body["metrics"]
    assert query.headers["authorization"] == "Bearer m"


def test_truncated_result_is_warned() -> None:
    truncated = json.loads(json.dumps(ANALYTICS))
    truncated["data"]["metadata"]["truncated"] = True

    report, _ = _run(
        OpenRouterProvider(management_key="m", api_key="k", production_keys=[]),
        lambda req: httpx.Response(200, json=truncated),
    )

    assert any("incompleta" in w for w in report.warnings)


def test_without_management_key_it_says_what_the_engine_key_can_tell() -> None:
    report, seen = _run(OpenRouterProvider(management_key=None, api_key="k", production_keys=[]), _ok)

    assert report.status is Status.NOT_CONNECTED
    assert "OPENROUTER_MANAGEMENT_KEY" in (report.how_to_connect or "")
    assert any("0.29" in n for n in report.notes)
    assert all(not r.url.path.endswith("/analytics/query") for r in seen)
