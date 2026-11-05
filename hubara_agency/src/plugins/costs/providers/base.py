"""What every provider adapter shares: the port and the HTTP error mapping.

Each adapter is a small class with an ``id``, a ``label``, ``connected`` and
an async ``fetch(client, rng, now)``. The ``httpx.AsyncClient`` is injected,
so tests drive adapters with ``httpx.MockTransport`` and recorded shapes, and
the service owns timeouts. Secrets go in headers, never in URLs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Protocol

import httpx

from src.plugins.costs.domain.period import BOGOTA, DateRange
from src.plugins.costs.domain.report import ProviderReport


class ProviderError(Exception):
    """A provider failure explained in plain Spanish (shown on the card)."""


class CostProvider(Protocol):
    id: str
    label: str

    @property
    def connected(self) -> bool: ...

    async def fetch(
        self, client: httpx.AsyncClient, rng: DateRange, now: datetime
    ) -> ProviderReport: ...


def bogota_day(unix_seconds: float) -> date:
    return datetime.fromtimestamp(unix_seconds, BOGOTA).date()


def utc_day(iso: str) -> date:
    """``2026-11-03T00:00:00Z`` / ``…000Z`` → its UTC calendar day."""
    return date.fromisoformat(iso[:10])


def capped_end(rng: DateRange, now: datetime) -> datetime:
    """End instant of the range, never in the future (some APIs refuse it)."""
    return min(rng.utc_end_exclusive, now.astimezone(timezone.utc))


def _error_message(resp: httpx.Response) -> str:
    try:
        body: Any = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
        for key in ("message", "msg", "detail"):
            if body.get(key):
                return str(body[key])
    return resp.reason_phrase or "sin detalle"


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider_label: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    data: dict[str, str] | None = None,
) -> Any:
    """One HTTP call → parsed JSON, or :class:`ProviderError` with a clear cause."""
    try:
        resp = await client.request(
            method, url, headers=headers, params=params, json=json, data=data
        )
    except httpx.TimeoutException:
        raise ProviderError(f"{provider_label} no respondió a tiempo.") from None
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"No se pudo conectar con {provider_label} ({type(exc).__name__})."
        ) from None
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"{provider_label} rechazó la credencial ({resp.status_code}): {_error_message(resp)}"
        )
    if resp.status_code >= 400:
        raise ProviderError(
            f"{provider_label} respondió {resp.status_code}: {_error_message(resp)}"
        )
    if not resp.content:
        raise ProviderError(
            f"{provider_label} respondió vacío ({resp.status_code}); intenta en un minuto."
        )
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"{provider_label} no respondió JSON.") from None
