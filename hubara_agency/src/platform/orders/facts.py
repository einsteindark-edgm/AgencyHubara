"""OrderFacts — los datos de negocio de un pedido, UNA sola lectura para todo el dashboard.

Por qué existe (pedido #31, 2026-09-17): el total de una orden se editó en
Medusa. Orders mostró el valor nuevo (lee Medusa en vivo) y Ads el viejo: lo
leía de `episode.order_total_cop`, una copia congelada en el chat cuando el bot
registró la venta. Dos lecturas del mismo dato → dos números.

Regla ("variables globales" del dashboard): total, estado de pago, etapa,
cliente y moneda de un pedido se leen SIEMPRE de acá. El vault guarda el
VÍNCULO (qué conversación / anuncio trajo qué `order_id`), nunca el valor.

Cómo es una sola variable y no una copia más:

  * **Un writer**: el `OrderQueryPort` canónico — el mismo que sirve la vista
    Orders — viene envuelto en `RecordingOrderQuery`, que graba en el store
    cada orden que lista o lee. Lo que ve Orders es lo que leen los demás.
  * **N readers** vía `OrderFactsReadPort.get_facts(ids)` (Ads, Campañas…).
    Lo que falta en el store se busca en Medusa (paginando hasta encontrarlo);
    lo vencido se sirve al instante y se refresca en segundo plano.
  * **Invalidación por evento**: toda mutación del dashboard publica `orders`
    en el bus → el store marca sus valores como sucios y el próximo read
    vuelve a Medusa (bloqueante). Lo editado directo en Medusa Admin (sin
    evento) aparece al vencer el TTL.
  * **Medusa caído**: se sirve el último valor conocido con `stale=True`; si
    nunca se conoció, el id queda en `unresolved` y el consumidor usa su
    respaldo (el total congelado) marcándolo como desactualizado.

R-DIP: platform puro — depende del `OrderQueryPort` (abstracción) y del bus
del dashboard. Los plugins lo consumen vía `src.sdk.connectorkit` (P-28).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.platform.orders.query_port import (
    OrderDetailDTO,
    OrderListDTO,
    OrderQueryPort,
    OrderSummaryDTO,
)

log = logging.getLogger(__name__)

DEFAULT_TTL_S = 60.0
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 20


@dataclass(frozen=True)
class OrderFacts:
    """Los datos canónicos de un pedido (espejo de `OrderSummaryDTO`)."""

    order_id: str
    display_id: str
    total_cop: int
    currency_code: str
    pay_status: str   # paid | partial | pending | refund (ver resolve_pay_status)
    stage: str        # new | preparing | ready | shipping | delivered | cancelled
    customer: str
    is_draft: bool

    @classmethod
    def from_summary(cls, s: OrderSummaryDTO) -> OrderFacts:
        return cls(
            order_id=s.id,
            display_id=s.display_id,
            total_cop=int(s.total_cop),
            currency_code=s.currency_code,
            pay_status=s.pay_status,
            stage=s.status,
            customer=s.customer,
            is_draft=s.is_draft,
        )

    @property
    def counts_as_revenue(self) -> bool:
        """Venta cerrada: pago confirmado y no cancelada (mismo criterio que
        `ads/sales_join` y el badge "Pagado" de Orders)."""
        return self.pay_status == "paid" and self.stage != "cancelled"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class OrderFactsSnapshot:
    """Respuesta de `get_facts`.

    `facts`: lo conocido por id. `unresolved`: ids que no se pudieron
    confirmar ni descartar (Medusa caído / tope de páginas). `stale`: algún
    valor pedido no está al día — la UI debe avisarlo.
    Un id que no está en ninguno de los dos = confirmado inexistente en Medusa.
    """

    facts: Mapping[str, OrderFacts] = field(default_factory=dict)
    unresolved: frozenset[str] = frozenset()
    stale: bool = False

    def revenue_cop(self, order_id: str | None, *, frozen_total: Any) -> int | None:
        """Ingreso atribuible a `order_id`, o None si no es una venta cerrada.

        `frozen_total` (la copia del vault) SOLO se usa si Medusa no pudo
        responder por este pedido.
        """
        if not order_id:
            return None
        fact = self.facts.get(order_id)
        if fact is not None:
            return fact.total_cop if fact.counts_as_revenue else None
        if order_id in self.unresolved and _is_number(frozen_total):
            return int(frozen_total)
        return None


@runtime_checkable
class OrderFactsReadPort(Protocol):
    """Contrato de lectura de los datos canónicos de pedidos."""

    async def get_facts(self, order_ids: Iterable[str]) -> OrderFactsSnapshot:  # pragma: no cover
        ...

    def invalidate(self, order_id: str | None = None) -> None:  # pragma: no cover
        ...


@dataclass
class _Entry:
    facts: OrderFacts | None  # None = confirmado inexistente
    fetched_at: float
    dirty: bool = False
    failed: bool = False


class OrderFactsStore:
    """Adapter real: caché compartida alimentada por el `OrderQueryPort`."""

    def __init__(
        self,
        query: OrderQueryPort,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        clock: Callable[[], float] = time.monotonic,
        bus: Any | None = None,
    ) -> None:
        self._query = query
        self._ttl_s = ttl_s
        self._page_size = page_size
        self._max_pages = max_pages
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._by_display: dict[str, str] = {}
        self._fetch_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        if bus is not None:
            bus.add_listener(self._on_dashboard_event)

    # ---- writer side ----

    def record(self, summary: OrderSummaryDTO) -> None:
        fact = OrderFacts.from_summary(summary)
        self._entries[fact.order_id] = _Entry(fact, self._clock())
        self._by_display[fact.display_id] = fact.order_id

    def record_list(self, page: OrderListDTO) -> None:
        if not page.catalog_available:
            return
        for summary in page.orders:
            self.record(summary)

    def invalidate(self, order_id: str | None = None) -> None:
        """Marca valores como sucios: el próximo read vuelve a Medusa (y si
        Medusa no responde, sirve el último valor con `stale`)."""
        target = self._by_display.get(order_id, order_id) if order_id else None
        if target is not None and target in self._entries:
            self._entries[target].dirty = True
            return
        for entry in self._entries.values():
            entry.dirty = True

    def _on_dashboard_event(self, event: Any) -> None:
        if getattr(event, "domain", None) == "orders":
            self.invalidate(getattr(event, "id", None))

    # ---- reader side ----

    def _needs_blocking(self, order_id: str) -> bool:
        entry = self._entries.get(order_id)
        return entry is None or entry.dirty

    def _is_expired(self, entry: _Entry) -> bool:
        return self._clock() - entry.fetched_at > self._ttl_s

    async def get_facts(self, order_ids: Iterable[str]) -> OrderFactsSnapshot:
        wanted = {i for i in order_ids if isinstance(i, str) and i}
        for _ in range(2):  # 2da vuelta: otro fetch en vuelo cubría otros ids
            blocking = {i for i in wanted if self._needs_blocking(i)}
            if not blocking:
                break
            await self._shared_fetch(blocking)
        expired = {
            i for i in wanted
            if (e := self._entries.get(i)) is not None and self._is_expired(e)
        }
        if expired and (self._refresh_task is None or self._refresh_task.done()):
            self._refresh_task = asyncio.create_task(self._fetch(expired))
        return self._snapshot(wanted)

    async def wait_for_refresh(self) -> None:
        if self._refresh_task is not None:
            await asyncio.shield(self._refresh_task)

    def _snapshot(self, wanted: set[str]) -> OrderFactsSnapshot:
        facts: dict[str, OrderFacts] = {}
        unresolved: set[str] = set()
        stale = False
        for order_id in wanted:
            entry = self._entries.get(order_id)
            if entry is None or (entry.facts is None and entry.failed):
                unresolved.add(order_id)
                stale = True
                continue
            if entry.failed:
                stale = True
            if entry.facts is not None:
                facts[order_id] = entry.facts
        return OrderFactsSnapshot(facts=facts, unresolved=frozenset(unresolved), stale=stale)

    async def _shared_fetch(self, ids: set[str]) -> None:
        if self._fetch_task is None or self._fetch_task.done():
            self._fetch_task = asyncio.create_task(self._fetch(ids))
        await asyncio.shield(self._fetch_task)

    def _mark_failed(self, ids: set[str]) -> None:
        now = self._clock()
        for order_id in ids:
            entry = self._entries.get(order_id)
            if entry is None:
                self._entries[order_id] = _Entry(None, now, failed=True)
            else:
                entry.failed = True
                entry.dirty = False  # no reintentar en cada read; el TTL manda
                entry.fetched_at = now - self._ttl_s - 1  # vencido → refresh en 2do plano

    async def _fetch(self, ids: set[str]) -> None:
        """Pagina el listado hasta encontrar `ids`. Graba TODO lo que ve."""
        found: set[str] = set()
        offset = 0
        exhausted = False
        try:
            for _ in range(self._max_pages):
                page = await self._query.list(
                    limit=self._page_size, offset=offset, include_drafts=True
                )
                if not page.catalog_available:
                    self._mark_failed(ids)
                    return
                self.record_list(page)
                found |= {s.id for s in page.orders} & ids
                offset += self._page_size
                if not page.orders or offset >= page.count:
                    exhausted = True
                    break
                if ids <= found:
                    break
        except Exception:  # noqa: BLE001 — Medusa caído no tumba al lector
            log.exception("OrderFactsStore: no pude leer órdenes de Medusa")
            self._mark_failed(ids - found)
            return
        missing = ids - found
        if exhausted:
            now = self._clock()
            for order_id in missing:
                self._entries[order_id] = _Entry(None, now)
        else:
            # Tope de páginas: no se puede afirmar que no existe. Un valor ya
            # conocido se sigue sirviendo (marcado stale); uno nunca visto
            # queda `unresolved`.
            known = {i for i in missing if (e := self._entries.get(i)) and e.facts}
            self._mark_failed(known)
            for order_id in missing - known:
                self._entries.pop(order_id, None)
            if missing:
                log.warning(
                    "OrderFactsStore: %d pedidos fuera del tope de páginas",
                    len(missing),
                )


class RecordingOrderQuery:
    """`OrderQueryPort` canónico: delega en el adapter real y graba en el
    store cada orden que lee. Es lo que devuelve `get_order_query_port()`."""

    def __init__(self, query: OrderQueryPort, store: OrderFactsStore) -> None:
        self._query = query
        self._store = store

    async def list(
        self, *, limit: int = 50, offset: int = 0, include_drafts: bool = True
    ) -> OrderListDTO:
        page = await self._query.list(
            limit=limit, offset=offset, include_drafts=include_drafts
        )
        self._store.record_list(page)
        return page

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        detail = await self._query.get(order_id)
        if detail is not None:
            self._store.record(detail.summary)
        return detail

    def __getattr__(self, name: str) -> Any:
        return getattr(self._query, name)


class InMemoryOrderFacts:
    """Fake oficial: mismos contratos que `OrderFactsStore` (contract suite)."""

    def __init__(self, facts: Iterable[OrderFacts] = (), *, available: bool = True) -> None:
        self._facts = {f.order_id: f for f in facts}
        self.available = available
        self.invalidations: list[str | None] = []

    async def get_facts(self, order_ids: Iterable[str]) -> OrderFactsSnapshot:
        wanted = {i for i in order_ids if isinstance(i, str) and i}
        if not self.available:
            return OrderFactsSnapshot(unresolved=frozenset(wanted), stale=bool(wanted))
        return OrderFactsSnapshot(
            facts={i: self._facts[i] for i in wanted if i in self._facts}
        )

    def invalidate(self, order_id: str | None = None) -> None:
        self.invalidations.append(order_id)
