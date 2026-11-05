"""The official COP/USD rate of each day (TRM), from datos.gov.co.

The Superintendencia Financiera publishes the TRM as open data (dataset
``32sa-8pi3``): one row per publication with ``vigenciadesde`` /
``vigenciahasta``, so weekends and holidays are covered by the Friday rate.
If it cannot be read, the configured fallback is used and the note says so.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx

from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.money import to_decimal
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.providers.base import ProviderError, request_json

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"


def _per_usd_note(per_usd: dict[str, Decimal]) -> str:
    if not per_usd:
        return ""
    rates = ", ".join(f"{cur} {rate} por dólar" for cur, rate in sorted(per_usd.items()))
    return f" Otras monedas con tasa fija de la configuración ({rates})."


class TrmSource:
    def __init__(
        self, *, fallback_cop_per_usd: Decimal, per_usd: dict[str, Decimal]
    ) -> None:
        self._fallback = fallback_cop_per_usd
        self._per_usd = dict(per_usd)

    async def load(self, client: httpx.AsyncClient, rng: DateRange) -> FxTable:
        where = (
            f"vigenciahasta >= '{rng.start.isoformat()}T00:00:00' AND "
            f"vigenciadesde <= '{rng.end.isoformat()}T00:00:00'"
        )
        try:
            rows = await request_json(
                client,
                "GET",
                TRM_URL,
                provider_label="datos.gov.co",
                params={"$where": where, "$order": "vigenciadesde ASC", "$limit": 500},
            )
            rates = _expand(rows)
        except (ProviderError, ValueError, KeyError, TypeError):
            rates = {}
        if not rates:
            return FxTable(
                cop_per_usd={rng.start: self._fallback},
                per_usd=self._per_usd,
                note=(
                    "No se pudo leer la TRM oficial: los pesos se convirtieron con la tasa de "
                    f"respaldo de {self._fallback} COP por dólar."
                    + _per_usd_note(self._per_usd)
                ),
            )
        return FxTable(
            cop_per_usd=rates,
            per_usd=self._per_usd,
            note=(
                "Pesos ↔ dólares con la TRM oficial de cada día (Superintendencia Financiera)."
                + _per_usd_note(self._per_usd)
            ),
        )


def _expand(rows: list[dict]) -> dict[date, Decimal]:
    out: dict[date, Decimal] = {}
    for row in rows:
        first = date.fromisoformat(str(row["vigenciadesde"])[:10])
        last = date.fromisoformat(str(row["vigenciahasta"])[:10])
        value = to_decimal(row["valor"])
        day = first
        while day <= last:
            out[day] = value
            day += timedelta(days=1)
    return out
