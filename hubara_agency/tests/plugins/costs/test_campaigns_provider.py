"""Campaigns card: Meta ad spend + WhatsApp campaigns, from the Ads section (cast)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.base import FetchContext, ProviderError
from src.plugins.costs.providers.campaigns import AdsCampaignsReader, CampaignsProvider
from src.plugins.costs.providers.whatsapp import WhatsAppProvider

RANGE = DateRange(date(2026, 10, 22), date(2026, 11, 4))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)

ADS_CAMPAIGNS = {
    "orders_stale": False,
    "campaigns": [
        {"id": "120200001", "name": "Velas Navidad", "source_type": "ad", "spend": 85000.5,
         "meta_campaign_id": "120200001", "messaging_conversations_started": 120,
         "whatsapp_send": None},
        {"id": "120200002", "name": "Sin gasto", "source_type": "ad", "spend": None,
         "meta_campaign_id": "120200002", "whatsapp_send": None},
        {"id": "mkt-001", "name": "Recompra octubre", "source_type": "hubara_campaign",
         "spend": None, "meta_campaign_id": None,
         "whatsapp_send": {"sent": 60, "delivered": 58, "read": 40, "failed": 2, "replied": 5,
                           "opted_out": 1, "cost_usd_micros": 750_000, "cost_pending": 3,
                           "untracked": 0}},
        {"id": "AD_web", "name": "Tienda web", "source_type": "web_referral", "spend": None,
         "whatsapp_send": None},
    ],
}


class FakeCast:
    def __init__(self, payload=ADS_CAMPAIGNS, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload = payload
        self.error = error

    async def __call__(self, path: str, params: dict):
        self.calls.append((path, params))
        if self.error:
            raise self.error
        return self.payload


def _fetch(provider, ctx: FetchContext | None, handler=None):
    async def go():
        transport = httpx.MockTransport(handler or (lambda r: httpx.Response(500)))
        async with httpx.AsyncClient(transport=transport) as client:
            return await provider.fetch(client, RANGE, NOW, ctx)

    return asyncio.run(go())


def test_meta_ads_spend_per_campaign_in_the_ad_account_currency() -> None:
    cast = FakeCast()
    report = _fetch(CampaignsProvider(AdsCampaignsReader(), meta_currency="COP"), FetchContext(cast_get=cast))

    assert report.status is Status.OK
    assert report.source is Source.METERED
    meta = {ln.key: ln for ln in report.lines}["meta:120200001"]
    assert meta.label == "Velas Navidad"
    assert meta.currency == "COP"
    assert meta.amount_micros == to_micros("85000.5")
    assert meta.group == "Anuncios de Meta"
    assert meta.quantity == 120
    assert "meta:120200002" not in {ln.key for ln in report.lines}


def test_whatsapp_campaigns_go_in_the_same_card() -> None:
    report = _fetch(CampaignsProvider(AdsCampaignsReader(), meta_currency="COP"), FetchContext(cast_get=FakeCast()))

    wa = {ln.key: ln for ln in report.lines}["whatsapp:mkt-001"]
    assert (wa.label, wa.currency, wa.amount_micros) == ("Recompra octubre", "USD", 750_000)
    assert (wa.quantity, wa.unit) == (60, "mensajes")
    assert wa.group == "Campañas de WhatsApp"
    assert any("3 mensajes" in w for w in report.warnings)


def test_the_cast_asks_ads_for_the_same_bogota_days() -> None:
    cast = FakeCast()
    _fetch(CampaignsProvider(AdsCampaignsReader(), meta_currency="COP"), FetchContext(cast_get=cast))

    assert cast.calls == [("/api/ads/campaigns", {"from": "2026-10-22", "to": "2026-11-04"})]


def test_ads_failure_is_an_error_card() -> None:
    cast = FakeCast(error=ProviderError("Ads respondió 502: provider no disponible"))
    report = _fetch(CampaignsProvider(AdsCampaignsReader(), meta_currency="COP"), FetchContext(cast_get=cast))

    assert report.status is Status.ERROR
    assert "Ads" in (report.error or "")


def test_whatsapp_card_takes_out_the_campaigns_so_nothing_counts_twice() -> None:
    reader = AdsCampaignsReader()
    meta = {"currency": "COP", "pricing_analytics": {"data": [{"data_points": [
        {"start": 1792731600, "pricing_type": "REGULAR", "pricing_category": "MARKETING",
         "volume": 60, "cost": 3000}]}]}}
    provider = WhatsAppProvider(token="t", waba_id="999", campaigns=reader)

    report = _fetch(provider, FetchContext(cast_get=FakeCast()), lambda r: httpx.Response(200, json=meta))

    minus = {ln.key: ln for ln in report.lines}["campaigns"]
    assert (minus.currency, minus.amount_micros) == ("USD", -750_000)
    assert "Campañas" in minus.label


def test_whatsapp_card_warns_when_it_cannot_take_out_the_campaigns() -> None:
    reader = AdsCampaignsReader()
    meta = {"currency": "COP", "pricing_analytics": {"data": []}}
    provider = WhatsAppProvider(token="t", waba_id="999", campaigns=reader)
    cast = FakeCast(error=ProviderError("Ads no respondió"))

    report = _fetch(provider, FetchContext(cast_get=cast), lambda r: httpx.Response(200, json=meta))

    assert report.status is Status.OK
    assert any("dos veces" in w for w in report.warnings)


def test_both_cards_share_one_call_to_ads() -> None:
    reader = AdsCampaignsReader()
    cast = FakeCast()
    ctx = FetchContext(cast_get=cast)
    meta = {"currency": "COP", "pricing_analytics": {"data": []}}

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=meta))
        async with httpx.AsyncClient(transport=transport) as client:
            await asyncio.gather(
                CampaignsProvider(reader, meta_currency="COP").fetch(client, RANGE, NOW, ctx),
                WhatsAppProvider(token="t", waba_id="999", campaigns=reader).fetch(client, RANGE, NOW, ctx),
            )

    asyncio.run(go())
    assert len(cast.calls) == 1
