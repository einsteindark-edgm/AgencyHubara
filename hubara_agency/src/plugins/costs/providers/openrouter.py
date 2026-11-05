"""OpenRouter (Jev, the decision engine's oracle): spend by API key and day.

Source: ``POST openrouter.ai/api/v1/analytics/query`` — it needs a
*management* key (``OPENROUTER_MANAGEMENT_KEY``); the engine's inference key
gets 403. Without it the card still shows what ``GET /api/v1/key`` tells about
the engine key (today / last 7 days / this month), which cannot be filtered by
date and therefore never enters the total.
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import (
    ProviderError,
    capped_end,
    request_json,
    utc_day,
)

API_URL = "https://openrouter.ai/api/v1"

_HOW_TO_CONNECT = (
    "Para ver el gasto por fechas hace falta una llave de administración: créala en "
    "openrouter.ai → Settings → Management keys (con vencimiento) y guárdala como "
    "OPENROUTER_MANAGEMENT_KEY (SSM del tenant)."
)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_analytics(payload: dict, production_keys: frozenset[str]) -> tuple[list[CostLine], bool]:
    data = payload.get("data") or {}
    daily: dict[str, dict[date, int]] = {}
    requests: dict[str, int] = {}
    for row in data.get("data") or []:
        key = str(row.get("api_key_id") or "sin llave")
        micros = to_micros(row.get("total_usage") or 0)
        day = utc_day(str(row.get("date__day") or ""))
        by_day = daily.setdefault(key, {})
        by_day[day] = by_day.get(day, 0) + micros
        requests[key] = requests.get(key, 0) + int(float(row.get("request_count") or 0))
    lines = [
        CostLine(
            key=key,
            label=key,
            currency="USD",
            daily=by_day,
            production=not production_keys or key in production_keys,
            group="Llave de API",
            quantity=requests.get(key),
            unit="solicitudes",
        )
        for key, by_day in daily.items()
    ]
    truncated = bool((data.get("metadata") or {}).get("truncated"))
    return sorted(lines, key=lambda ln: (not ln.production, -ln.amount_micros)), truncated


class OpenRouterProvider:
    id = "openrouter"
    label = "OpenRouter (Jev)"

    def __init__(
        self,
        *,
        management_key: str | None,
        api_key: str | None,
        production_keys: list[str] | frozenset[str],
    ) -> None:
        self._management_key = management_key
        self._api_key = api_key
        self._production = frozenset(production_keys)

    @property
    def connected(self) -> bool:
        return bool(self._management_key)

    async def _engine_key_note(self, client: httpx.AsyncClient) -> str | None:
        if not self._api_key:
            return None
        try:
            payload = await request_json(
                client,
                "GET",
                f"{API_URL}/key",
                provider_label="OpenRouter",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except ProviderError:
            return None
        info = payload.get("data") or {}
        return (
            "La llave del motor solo informa totales sin fechas: hoy US$"
            f"{info.get('usage_daily', 0)}, últimos 7 días US${info.get('usage_weekly', 0)}, "
            f"este mes US${info.get('usage_monthly', 0)}."
        )

    async def fetch(
        self, client: httpx.AsyncClient, rng: DateRange, now: datetime
    ) -> ProviderReport:
        if not self.connected:
            note = await self._engine_key_note(client)
            return ProviderReport.not_connected(
                self.id, rng, how_to_connect=_HOW_TO_CONNECT, notes=[note] if note else []
            )
        body = {
            "metrics": ["total_usage", "request_count"],
            "dimensions": ["api_key_id"],
            "granularity": "day",
            "time_range": {"start": _ts(rng.utc_start), "end": _ts(capped_end(rng, now))},
            "limit": 1000,
        }
        try:
            payload = await request_json(
                client,
                "POST",
                f"{API_URL}/analytics/query",
                provider_label="OpenRouter",
                headers={"Authorization": f"Bearer {self._management_key}"},
                json=body,
            )
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        lines, truncated = parse_analytics(payload, self._production)
        warnings = (
            ["OpenRouter devolvió una respuesta incompleta (demasiadas filas): acorta el rango."]
            if truncated
            else []
        )
        return ProviderReport.ok(
            self.id,
            rng,
            Source.METERED,
            lines,
            notes=["Gasto de OpenRouter por llave; OpenRouter agrupa por día UTC."],
            warnings=warnings,
        )
