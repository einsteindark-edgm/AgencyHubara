"""WhatsApp: Meta's pricing_analytics of the WABA, by day and category."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.whatsapp import WhatsAppProvider

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
NOV2 = 1793595600  # 2026-11-02 00:00 Bogotá
NOV3 = 1793682000

PAYLOAD = {
    "id": "999",
    "currency": "COP",
    "timezone_id": "43",
    "pricing_analytics": {
        "data": [
            {
                "data_points": [
                    {"start": NOV2, "end": NOV3, "pricing_type": "REGULAR",
                     "pricing_category": "MARKETING", "volume": 2, "cost": 92.0454},
                    {"start": NOV3, "end": NOV3 + 86400, "pricing_type": "REGULAR",
                     "pricing_category": "MARKETING", "volume": 1, "cost": 46.0227},
                    {"start": NOV3, "end": NOV3 + 86400, "pricing_type": "REGULAR",
                     "pricing_category": "UTILITY", "volume": 3, "cost": 8.8365},
                    {"start": NOV2, "end": NOV3, "pricing_type": "FREE_ENTRY_POINT",
                     "pricing_category": "SERVICE", "volume": 400, "cost": 0},
                    {"start": NOV2, "end": NOV3, "pricing_type": "REGULAR",
                     "pricing_category": "SOMETHING_NEW", "volume": 1, "cost": 1.5},
                ]
            }
        ]
    },
}


def _run(provider: WhatsAppProvider, handler) -> tuple:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as client:
            return await provider.fetch(client, RANGE, NOW)

    return asyncio.run(go()), seen


def test_marketing_cost_is_summed_per_category_in_the_waba_currency() -> None:
    report, _ = _run(
        WhatsAppProvider(token="t", waba_id="999"),
        lambda req: httpx.Response(200, json=PAYLOAD),
    )

    assert report.status is Status.OK
    assert report.source is Source.METERED
    lines = {ln.key: ln for ln in report.lines}
    marketing = lines["MARKETING:REGULAR"]
    assert marketing.currency == "COP"
    assert marketing.label == "Marketing"
    assert marketing.amount_micros == to_micros("138.0681")
    assert marketing.quantity == 3
    assert marketing.daily == {
        date(2026, 11, 2): to_micros("92.0454"),
        date(2026, 11, 3): to_micros("46.0227"),
    }


def test_free_messages_are_listed_with_why_they_were_free() -> None:
    report, _ = _run(
        WhatsAppProvider(token="t", waba_id="999"),
        lambda req: httpx.Response(200, json=PAYLOAD),
    )

    free = {ln.key: ln for ln in report.lines}["SERVICE:FREE_ENTRY_POINT"]
    assert free.amount_micros == 0
    assert free.quantity == 400
    assert "anuncio" in free.label.lower()


def test_unknown_category_is_kept_not_dropped() -> None:
    report, _ = _run(
        WhatsAppProvider(token="t", waba_id="999"),
        lambda req: httpx.Response(200, json=PAYLOAD),
    )

    assert "SOMETHING_NEW:REGULAR" in {ln.key for ln in report.lines}


def test_request_asks_daily_analytics_for_the_bogota_range_without_token_in_url() -> None:
    _, seen = _run(
        WhatsAppProvider(token="secret-token", waba_id="999"),
        lambda req: httpx.Response(200, json=PAYLOAD),
    )

    (req,) = seen
    assert req.url.path.endswith("/999")
    fields = req.url.params["fields"]
    assert f"start({NOV2})" in fields
    assert f"end({NOV3 + 86400})" in fields
    assert "granularity(DAILY)" in fields
    assert "secret-token" not in str(req.url)
    assert req.headers["authorization"] == "Bearer secret-token"


def test_today_is_asked_only_up_to_now() -> None:
    rng = DateRange(date(2026, 11, 3), date(2026, 11, 4))
    seen: list[httpx.Request] = []

    async def go():
        def _h(req):
            seen.append(req)
            return httpx.Response(200, json=PAYLOAD)

        async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as client:
            return await WhatsAppProvider(token="t", waba_id="999").fetch(client, rng, NOW)

    asyncio.run(go())
    assert f"end({int(NOW.timestamp())})" in seen[0].url.params["fields"]


def test_without_token_it_says_how_to_connect() -> None:
    report, seen = _run(
        WhatsAppProvider(token=None, waba_id="999"),
        lambda req: httpx.Response(500),
    )

    assert report.status is Status.NOT_CONNECTED
    assert seen == []


def test_meta_error_is_shown_in_plain_words() -> None:
    report, _ = _run(
        WhatsAppProvider(token="t", waba_id="999"),
        lambda req: httpx.Response(
            400, json={"error": {"message": "(#100) Invalid parameter", "code": 100}}
        ),
    )

    assert report.status is Status.ERROR
    assert "Invalid parameter" in (report.error or "")
