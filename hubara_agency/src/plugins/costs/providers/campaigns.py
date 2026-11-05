"""Campaigns card: what each campaign cost — Meta ads and WhatsApp campaigns.

Source: the Ads section's own contract, ``GET /api/ads/campaigns?from&to``,
reached by cast (``castkit``, declared in the manifest as ``campaigns@v1``):

* a Meta ad campaign row carries ``spend`` — what Meta Ads billed in the
  window, in the ad account currency (COP for Hubara), without taxes;
* a WhatsApp campaign row (``source_type == "hubara_campaign"``, plugin
  marketing) carries ``whatsapp_send`` — the messages sent in the window and
  ``cost_usd_micros``, Meta's price of those messages.

Ads gives one figure per campaign for the whole window (no daily split), so
the lines are *undated*. The WhatsApp card reads the same rows to take the
WhatsApp campaigns out of Meta's WhatsApp total — they are counted here, once.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import FetchContext, ProviderError
from src.sdk.runtime import BoundedTTLCache

ADS_CAMPAIGNS_PATH = "/api/ads/campaigns"
WHATSAPP_CAMPAIGN = "hubara_campaign"
_SHARE_TTL_S = 120.0

_NO_CAST = "Esta tarjeta lee la sección Ads del dashboard; ábrela desde la pestaña Costos."


@dataclass(frozen=True)
class MetaAdSpend:
    campaign_id: str
    name: str
    spend_micros: int
    conversations: int | None


@dataclass(frozen=True)
class WhatsAppCampaignCost:
    campaign_id: str
    name: str
    cost_usd_micros: int
    sent: int
    pending: int


@dataclass(frozen=True)
class CampaignSpend:
    meta: tuple[MetaAdSpend, ...] = ()
    whatsapp: tuple[WhatsAppCampaignCost, ...] = ()

    @property
    def whatsapp_usd_micros(self) -> int:
        return sum(c.cost_usd_micros for c in self.whatsapp)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def parse_ads_campaigns(payload: dict) -> CampaignSpend:
    meta: dict[str, MetaAdSpend] = {}
    whatsapp: list[WhatsAppCampaignCost] = []
    for row in payload.get("campaigns") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("id") or "Sin nombre")
        if row.get("source_type") == WHATSAPP_CAMPAIGN:
            send = row.get("whatsapp_send")
            if isinstance(send, dict) and _int(send.get("sent")):
                whatsapp.append(
                    WhatsAppCampaignCost(
                        campaign_id=str(row.get("id")),
                        name=name,
                        cost_usd_micros=_int(send.get("cost_usd_micros")),
                        sent=_int(send.get("sent")),
                        pending=_int(send.get("cost_pending")),
                    )
                )
            continue
        spend = row.get("spend")
        if isinstance(spend, bool) or not isinstance(spend, (int, float)) or spend <= 0:
            continue
        key = str(row.get("meta_campaign_id") or row.get("id"))
        if key in meta:  # one Meta campaign can come back in more than one row
            continue
        conversations = row.get("messaging_conversations_started")
        meta[key] = MetaAdSpend(
            campaign_id=key,
            name=name,
            spend_micros=to_micros(spend),
            conversations=_int(conversations) if conversations is not None else None,
        )
    return CampaignSpend(meta=tuple(meta.values()), whatsapp=tuple(whatsapp))


@dataclass
class AdsCampaignsReader:
    """One call to Ads per range, shared by the Campaigns and WhatsApp cards."""

    _cache: BoundedTTLCache = field(
        default_factory=lambda: BoundedTTLCache(ttl_s=_SHARE_TTL_S, max_entries=16)
    )
    _locks: dict[tuple[date, date], asyncio.Lock] = field(default_factory=dict)

    async def spend(self, rng: DateRange, ctx: FetchContext | None) -> CampaignSpend:
        if ctx is None or ctx.cast_get is None:
            raise ProviderError(_NO_CAST)
        key = (rng.start, rng.end)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit is None:
                payload = await ctx.cast_get(
                    ADS_CAMPAIGNS_PATH,
                    {"from": rng.start.isoformat(), "to": rng.end.isoformat()},
                )
                if not isinstance(payload, dict):
                    raise ProviderError("La sección Ads respondió algo que no se pudo leer.")
                hit = parse_ads_campaigns(payload)
                self._cache.put(key, hit)
        if not lock.locked():
            self._locks.pop(key, None)
        return hit


class CampaignsProvider:
    id = "campaigns"
    label = "Campañas"
    connected = True

    def __init__(self, reader: AdsCampaignsReader, *, meta_currency: str) -> None:
        self._reader = reader
        self._meta_currency = meta_currency.upper()

    async def fetch(
        self,
        client: httpx.AsyncClient,
        rng: DateRange,
        now: datetime,
        ctx: FetchContext | None = None,
    ) -> ProviderReport:
        if ctx is None or ctx.cast_get is None:
            return ProviderReport.not_connected(self.id, rng, how_to_connect=_NO_CAST)
        try:
            spend = await self._reader.spend(rng, ctx)
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        lines = [
            CostLine(
                key=f"meta:{ad.campaign_id}",
                label=ad.name,
                currency=self._meta_currency,
                daily={},
                undated=ad.spend_micros,
                group="Anuncios de Meta",
                quantity=ad.conversations,
                unit="conversaciones iniciadas" if ad.conversations is not None else None,
            )
            for ad in sorted(spend.meta, key=lambda a: -a.spend_micros)
        ] + [
            CostLine(
                key=f"whatsapp:{wa.campaign_id}",
                label=wa.name,
                currency="USD",
                daily={},
                undated=wa.cost_usd_micros,
                group="Campañas de WhatsApp",
                quantity=wa.sent,
                unit="mensajes",
            )
            for wa in sorted(spend.whatsapp, key=lambda c: -c.cost_usd_micros)
        ]
        warnings = [
            f"«{wa.name}»: {wa.pending} mensajes todavía sin precio de Meta (aún no suman)."
            for wa in spend.whatsapp
            if wa.pending
        ]
        return ProviderReport.ok(
            self.id,
            rng,
            Source.METERED,
            lines,
            notes=[
                "Anuncios: lo que Meta Ads cobró en el rango por cada campaña, sin impuestos.",
                "Campañas de WhatsApp: el precio de Meta de los mensajes enviados en el rango "
                "(la tarjeta de WhatsApp los descuenta para no contarlos dos veces).",
                "Meta da una cifra por campaña para todo el rango, sin desglose por día.",
            ],
            warnings=warnings,
        )
