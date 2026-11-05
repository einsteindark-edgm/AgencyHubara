"""DeepSeek: cost by API key, as the platform's "Usage" page shows it.

DeepSeek's public API has no usage or cost endpoint (only ``/user/balance``).
The per-key numbers live behind the platform's web session:
``platform.deepseek.com/api/v0/usage/by_api_key/{cost,amount}``, the same
calls the Usage page makes. They need the session token of the account
(``DEEPSEEK_PLATFORM_TOKEN``, read from the browser) and day-aligned instants
in Bogotá (``tz=-18000``). The account also bills some calls in CNY; each
currency comes as its own series.

Which keys are production is configuration (``costs.yaml``); the rest (lab,
local simulations) are shown apart so the production total is honest.
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.providers.base import ProviderError, bogota_day, request_json

PLATFORM_URL = "https://platform.deepseek.com/api/v0/usage/by_api_key"
BALANCE_URL = "https://api.deepseek.com/user/balance"
_TZ_OFFSET = "-18000"  # Bogotá, in seconds

_HOW_TO_CONNECT = (
    "El consumo por llave solo está en la página Usage de platform.deepseek.com. "
    "Abre esa página con tu sesión, en la consola del navegador corre "
    "copy(JSON.parse(localStorage.userToken).value) y guarda ese valor como "
    "DEEPSEEK_PLATFORM_TOKEN (SSM del tenant)."
)


def _biz_data(payload: dict) -> dict:
    data = payload.get("data") or {}
    if data.get("biz_code") not in (0, None) or data.get("biz_data") is None:
        msg = data.get("biz_msg") or payload.get("msg") or "respuesta sin datos"
        raise ProviderError(
            f"DeepSeek no entregó el consumo ({msg}). Si la sesión venció, vuelve a "
            "copiar el token de platform.deepseek.com en DEEPSEEK_PLATFORM_TOKEN."
        )
    return data["biz_data"]


def _requests_by_key(amount: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for series in amount.get("series") or []:
        tid = str((series.get("api_key") or {}).get("tracking_id") or "")
        for bucket in series.get("buckets") or []:
            out[tid] = out.get(tid, 0) + int((bucket.get("usage") or {}).get("REQUEST") or 0)
    return out


def parse_usage_by_key(
    cost: dict, amount: dict | None, production_keys: frozenset[str]
) -> list[CostLine]:
    requests = _requests_by_key(amount or {})
    daily: dict[tuple[str, str], dict[date, int]] = {}
    names: dict[str, str] = {}
    for block in cost.get("data") or []:
        currency = str(block.get("currency") or "USD").upper()
        for series in block.get("series") or []:
            key = series.get("api_key") or {}
            tid = str(key.get("tracking_id") or key.get("name") or "?")
            name = str(key.get("name") or "Sin nombre")
            names[tid] = name if key.get("valid", True) else f"{name} (llave borrada)"
            by_day = daily.setdefault((tid, currency), {})
            for bucket in series.get("buckets") or []:
                micros = to_micros(bucket.get("cost") or 0)
                if micros:
                    day = bogota_day(int(bucket["time"]))
                    by_day[day] = by_day.get(day, 0) + micros
    lines = []
    for (tid, currency), by_day in daily.items():
        if not by_day:
            continue
        name = names[tid]
        lines.append(
            CostLine(
                key=f"{tid}:{currency}",
                label=name,
                currency=currency,
                daily=by_day,
                production=name in production_keys,
                group="Llave de API",
                quantity=requests.get(tid) if currency == "USD" else None,
                unit="solicitudes" if currency == "USD" and tid in requests else None,
            )
        )
    return sorted(lines, key=lambda ln: (not ln.production, -ln.amount_micros, ln.label))


class DeepSeekProvider:
    id = "deepseek"
    label = "DeepSeek"

    def __init__(
        self,
        *,
        platform_token: str | None,
        api_key: str | None,
        production_keys: list[str] | frozenset[str],
    ) -> None:
        self._token = platform_token
        self._api_key = api_key
        self._production = frozenset(production_keys)

    @property
    def connected(self) -> bool:
        return bool(self._token)

    async def _balance_note(self, client: httpx.AsyncClient) -> str | None:
        if not self._api_key:
            return None
        try:
            payload = await request_json(
                client,
                "GET",
                BALANCE_URL,
                provider_label="DeepSeek",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except ProviderError:
            return None
        parts = [
            f"{info.get('currency')} {info.get('total_balance')}"
            for info in payload.get("balance_infos") or []
        ]
        return f"Saldo disponible hoy en la cuenta: {', '.join(parts)}." if parts else None

    async def _platform(self, client: httpx.AsyncClient, what: str, rng: DateRange) -> dict:
        payload = await request_json(
            client,
            "GET",
            f"{PLATFORM_URL}/{what}",
            provider_label="DeepSeek",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            params={
                "start": int(rng.utc_start.timestamp()),
                "end": int(rng.utc_end_exclusive.timestamp()),
                "tz": _TZ_OFFSET,
            },
        )
        return _biz_data(payload)

    async def fetch(
        self, client: httpx.AsyncClient, rng: DateRange, now: datetime
    ) -> ProviderReport:
        balance = await self._balance_note(client)
        notes = [balance] if balance else []
        if not self.connected:
            return ProviderReport.not_connected(
                self.id, rng, how_to_connect=_HOW_TO_CONNECT, notes=notes
            )
        try:
            cost = await self._platform(client, "cost", rng)
        except ProviderError as exc:
            return ProviderReport.failed(self.id, rng, str(exc))
        warnings: list[str] = []
        try:
            amount = await self._platform(client, "amount", rng)
        except ProviderError:
            amount = None
            warnings.append("No se pudo leer el número de solicitudes; el costo sí está.")
        notes.insert(0, "Cifras de la página Usage de DeepSeek, por llave y por día de Bogotá.")
        return ProviderReport.ok(
            self.id,
            rng,
            Source.METERED,
            parse_usage_by_key(cost, amount, self._production),
            notes=notes,
            warnings=warnings,
        )
