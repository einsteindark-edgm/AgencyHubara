"""The service in front of the providers — the reason the tab never hangs.

* One endpoint call = one provider: the page asks every card in parallel and
  each card loads (or fails) on its own.
* Every provider call has a hard time limit; past it the card says so.
* Answers are cached per (provider, range): briefly when the range includes
  today, longer when it is fully in the past. Concurrent asks for the same
  key share one upstream call. Errors are never cached.
* Any unexpected exception in an adapter becomes an error card, never a 500.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.period import BOGOTA, DateRange
from src.plugins.costs.domain.report import ProviderReport, Status
from src.plugins.costs.providers.base import CostProvider
from src.sdk.runtime import BoundedTTLCache

logger = logging.getLogger(__name__)

_MAX_CACHE_ENTRIES = 64
_FX_TIMEOUT_S = 10.0


class UnknownProvider(KeyError):
    """No provider with that id."""


class FxLoader(Protocol):
    async def load(self, client: httpx.AsyncClient, rng: DateRange) -> FxTable: ...


@dataclass(frozen=True)
class ServedReport:
    report: ProviderReport
    fetched_at: datetime
    cached: bool

    def to_json(self) -> dict:
        return {
            **self.report.to_json(),
            "fetched_at": self.fetched_at.isoformat(),
            "cached": self.cached,
        }


class CostsService:
    def __init__(
        self,
        providers: list[CostProvider],
        fx: FxLoader,
        *,
        timeout_s: float,
        live_ttl_s: float,
        closed_ttl_s: float,
        now: Callable[[], datetime],
    ) -> None:
        self._providers = {p.id: p for p in providers}
        self._fx = fx
        self._timeout_s = timeout_s
        self._now = now
        self._live: BoundedTTLCache = BoundedTTLCache(
            ttl_s=live_ttl_s, max_entries=_MAX_CACHE_ENTRIES
        )
        self._closed: BoundedTTLCache = BoundedTTLCache(
            ttl_s=closed_ttl_s, max_entries=_MAX_CACHE_ENTRIES
        )
        self._fx_live: BoundedTTLCache = BoundedTTLCache(ttl_s=live_ttl_s, max_entries=16)
        self._fx_closed: BoundedTTLCache = BoundedTTLCache(ttl_s=closed_ttl_s, max_entries=16)
        self._locks: dict[tuple, asyncio.Lock] = {}

    def now(self) -> datetime:
        return self._now()

    def catalog(self) -> list[dict]:
        return [
            {"id": p.id, "label": p.label, "connected": p.connected}
            for p in self._providers.values()
        ]

    def _is_live(self, rng: DateRange) -> bool:
        return rng.end >= self._now().astimezone(BOGOTA).date()

    def clear(self) -> None:
        for cache in (self._live, self._closed, self._fx_live, self._fx_closed):
            cache.clear()

    async def _fx_table(self, client: httpx.AsyncClient, rng: DateRange) -> FxTable:
        cache = self._fx_live if self._is_live(rng) else self._fx_closed
        key = (rng.start, rng.end)
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            table = await asyncio.wait_for(self._fx.load(client, rng), _FX_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — the loader has its own fallback
            logger.warning("costs: fx load failed", exc_info=True)
            return FxTable(cop_per_usd={}, note="No se pudo leer la tasa de cambio.")
        cache.put(key, table)
        return table

    async def _fetch(
        self, provider: CostProvider, client: httpx.AsyncClient, rng: DateRange
    ) -> ProviderReport:
        try:
            return await asyncio.wait_for(
                provider.fetch(client, rng, self._now()), self._timeout_s
            )
        except TimeoutError:
            return ProviderReport.failed(
                provider.id,
                rng,
                f"{provider.label} no respondió en {self._timeout_s:g} s; "
                "vuelve a intentar en un momento.",
            )
        except Exception:  # noqa: BLE001 — a card must never take the page down
            logger.exception("costs: provider %s crashed", provider.id)
            return ProviderReport.failed(
                provider.id,
                rng,
                f"La respuesta de {provider.label} no se pudo leer (error interno del tablero).",
            )

    async def report(
        self, provider_id: str, rng: DateRange, *, refresh: bool = False
    ) -> ServedReport:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise UnknownProvider(provider_id)
        cache = self._live if self._is_live(rng) else self._closed
        key = (provider_id, rng.start, rng.end)
        if not refresh:
            hit = cache.get(key)
            if hit is not None:
                return ServedReport(hit.report, hit.fetched_at, cached=True)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not refresh:
                hit = cache.get(key)
                if hit is not None:
                    return ServedReport(hit.report, hit.fetched_at, cached=True)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=10.0),
                headers={"User-Agent": "Hubara-Costs/1.0"},
            ) as client:
                raw, fx = await asyncio.gather(
                    self._fetch(provider, client, rng), self._fx_table(client, rng)
                )
            served = ServedReport(raw.priced(fx), self._now(), cached=False)
            if served.report.status is not Status.ERROR:
                cache.put(key, served)
        if not lock.locked():
            self._locks.pop(key, None)
        return served
