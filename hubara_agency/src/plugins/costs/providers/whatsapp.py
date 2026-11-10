"""WhatsApp: what Meta says the WABA spent, by Bogotá day and pricing category.

Source: ``GET /<WABA_ID>?fields=pricing_analytics…granularity(DAILY)`` of the
Graph API. Amounts come in the WABA currency (COP for Hubara) and exclude
taxes; Meta calls them approximate. Daily buckets follow the WABA timezone
(Bogotá), so they line up with the tab's days.
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import (
    FetchContext,
    ProviderError,
    bogota_day,
    capped_end,
    request_json,
)
from src.plugins.costs.providers.campaigns import AdsCampaignsReader
from src.sdk.connectorkit import graph_url

_CATEGORY = {
    "MARKETING": "Marketing",
    "MARKETING_LITE": "Marketing lite",
    "UTILITY": "Utilidad",
    "AUTHENTICATION": "Autenticación",
    "AUTHENTICATION_INTERNATIONAL": "Autenticación internacional",
    "SERVICE": "Servicio",
    "REFERRAL_CONVERSION": "Conversación desde anuncio",
}
_TYPE = {
    "REGULAR": None,
    "FREE_CUSTOMER_SERVICE": "gratis: ventana de atención al cliente",
    "FREE_ENTRY_POINT": "gratis: llegó por un anuncio",
    "FREE_TIER": "gratis: cupo mensual",
}

_HOW_TO_CONNECT = (
    "Falta WHATSAPP_ACCESS_TOKEN o WHATSAPP_BUSINESS_ACCOUNT_ID en el entorno del API."
)


def _label(category: str, pricing_type: str) -> str:
    cat = _CATEGORY.get(category, category.replace("_", " ").capitalize())
    why = _TYPE.get(pricing_type, pricing_type.replace("_", " ").lower())
    return cat if not why else f"{cat} ({why})"


def parse_pricing_analytics(payload: dict) -> list[CostLine]:
    currency = str(payload.get("currency") or "USD").upper()
    daily: dict[str, dict[date, int]] = {}
    volume: dict[str, int] = {}
    labels: dict[str, str] = {}
    blocks = (payload.get("pricing_analytics") or {}).get("data") or []
    for block in blocks:
        for point in block.get("data_points") or []:
            category = str(point.get("pricing_category") or "OTRO")
            ptype = str(point.get("pricing_type") or "REGULAR")
            key = f"{category}:{ptype}"
            day = bogota_day(int(point["start"]))
            by_day = daily.setdefault(key, {})
            by_day[day] = by_day.get(day, 0) + to_micros(point.get("cost") or 0)
            volume[key] = volume.get(key, 0) + int(point.get("volume") or 0)
            labels[key] = _label(category, ptype)
    lines = [
        CostLine(
            key=key,
            label=labels[key],
            currency=currency,
            daily=daily[key],
            group="Categoría de Meta",
            quantity=volume[key],
            unit="mensajes",
        )
        for key in daily
    ]
    # Paid first, then by amount: the card reads top-down.
    return sorted(lines, key=lambda ln: (-ln.amount_micros, ln.label))


class WhatsAppProvider:
    id = "whatsapp"
    label = "WhatsApp (Meta)"

    def __init__(
        self,
        *,
        token: str | None,
        waba_id: str | None,
        campaigns: AdsCampaignsReader | None = None,
    ) -> None:
        self._token = token
        self._waba_id = waba_id
        self._campaigns = campaigns

    @property
    def connected(self) -> bool:
        return bool(self._token and self._waba_id)

    async def _campaigns_deduction(
        self, rng: DateRange, ctx: FetchContext | None
    ) -> tuple[list[CostLine], list[str]]:
        """The WhatsApp campaigns, taken out: the Campaigns card counts them."""
        if self._campaigns is None:
            return [], []
        try:
            spend = await self._campaigns.spend(rng, ctx)
        except ProviderError:
            return [], [
                "No se pudo leer el costo de las campañas de WhatsApp: si la tarjeta "
                "Campañas sí las muestra, quedan contadas dos veces."
            ]
        if not spend.whatsapp_usd_micros:
            return [], []
        line = CostLine(
            key="campaigns",
            label="Menos: campañas de WhatsApp (van en la tarjeta Campañas)",
            currency="USD",
            daily={},
            undated=-spend.whatsapp_usd_micros,
            group="Descontado",
        )
        return [line], []

    async def fetch(
        self,
        client: httpx.AsyncClient,
        rng: DateRange,
        now: datetime,
        ctx: FetchContext | None = None,
    ) -> ProviderReport:
        if not self.connected:
            return ProviderReport.not_connected(self.id, rng, how_to_connect=_HOW_TO_CONNECT)
        start = int(rng.utc_start.timestamp())
        end = int(capped_end(rng, now).timestamp())
        fields = (
            "currency,"
            f"pricing_analytics.start({start}).end({end}).granularity(DAILY)"
            ".metric_types(COST,VOLUME).dimensions(PRICING_CATEGORY,PRICING_TYPE)"
        )
        try:
            payload = await request_json(
                client,
                "GET",
                graph_url(str(self._waba_id)),
                provider_label="Meta",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"fields": fields},
            )
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        deduction, warnings = await self._campaigns_deduction(rng, ctx)
        return ProviderReport.ok(
            self.id,
            rng,
            Source.METERED,
            parse_pricing_analytics(payload) + deduction,
            warnings=warnings,
            notes=[
                "Cifra de Meta para la cuenta de WhatsApp Business, sin impuestos.",
                "Meta la llama aproximada: la factura puede variar un poco.",
            ],
        )
