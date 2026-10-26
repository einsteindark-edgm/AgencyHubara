"""Dónde compran — las compras por ciudad y zona (barrio), por campaña.

Pedido del operador (2026-10-23): «saber la ciudad y zonas donde están
comprando por campaña, en cada campaña poder cotejar esos datos, y una vista
general de todas las compras».

De dónde sale cada dato (gotcha 13):

  * la compra, su valor, su ciudad y su barrio → el pedido de Orders
    (`OrderSummaryDTO.city` + `.neighborhood` = `shipping_address.address_2`,
    donde el formulario de envío guarda el barrio);
  * la campaña → el VÍNCULO conversación→pedido del vault, con la misma regla
    de atribución que el resto de Ads (`_episode_to_campaign_id`). Un pedido
    sin conversación (tienda web, pedido a mano) va a su propio grupo.

Compra = pedido registrado (borrador incluido: el bot registra borradores)
que no está cancelado ni es de prueba. El pago no la define — un contra
entrega es una compra antes de pagarse; `paid_orders` lo cuenta aparte.

La ciudad y el barrio son texto libre del cliente: se agrupan por una llave
normalizada (`place_key`) y se muestran con la forma más escrita.

PURO salvo `collect_orders_in_window` (async, recibe el port por parámetro).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.plugins.ads.aggregation import (
    DIRECT_CAMPAIGN_ID,
    DIRECT_CAMPAIGN_NAME,
    WEB_CART_CAMPAIGN_ID,
    WEB_CART_CAMPAIGN_NAME,
    _attributed_campaign_touch,
    _episode_headline,
    _episode_to_campaign_id,
)

#: Grupo de los pedidos que ninguna conversación reclama.
UNLINKED_CAMPAIGN_ID = "sin_conversacion"
UNLINKED_CAMPAIGN_NAME = "Sin conversación · tienda web o pedido a mano"


@dataclass(frozen=True)
class CampaignLink:
    """La campaña (bucket del vault) que trajo un pedido."""

    id: str
    name: str | None


@dataclass(frozen=True)
class PurchaseRow:
    """Una compra con su lugar y su campaña ya resueltos."""

    order_id: str
    city: str | None
    neighborhood: str | None
    value_cop: int
    paid: bool
    campaign_id: str
    campaign_name: str | None
    # Bucket crudo del vault (el anuncio): el drill-down por segmento/anuncio
    # filtra por acá. None = pedido sin conversación.
    source_id: str | None = None


@dataclass(frozen=True)
class ZonePurchases:
    zone: str | None  # None = el pedido no trae barrio
    orders: int
    value_cop: int


@dataclass(frozen=True)
class CampaignPurchases:
    id: str
    name: str | None
    orders: int
    value_cop: int


@dataclass(frozen=True)
class CityPurchases:
    key: str
    city: str
    orders: int
    paid_orders: int
    value_cop: int
    zones: list[ZonePurchases] = field(default_factory=list)
    campaigns: list[CampaignPurchases] = field(default_factory=list)
    # Compras de esta ciudad en TODAS las campañas — el cotejo de una campaña
    # contra el total. None en la vista general.
    overall_orders: int | None = None


@dataclass(frozen=True)
class PurchasePlaces:
    orders: int
    paid_orders: int
    value_cop: int
    without_city: int
    cities: list[CityPurchases] = field(default_factory=list)
    overall_orders: int | None = None


@dataclass(frozen=True)
class OrdersInWindow:
    orders: list[Any]
    available: bool = True
    truncated: bool = False


# "Bogotá D.C." / "Bogota DC": el sufijo del distrito no es otra ciudad.
_DISTRICT_SUFFIX = re.compile(r"\s+d\.?\s*c\.?$", re.IGNORECASE)
_NOT_A_WORD = re.compile(r"[^\w]+")


def _head(text: str | None) -> str:
    """Lo que el cliente escribió como lugar, sin el resto: "Chía,
    Cundinamarca" → "Chía"; "Bogotá D.C." → "Bogotá"."""
    if not isinstance(text, str):
        return ""
    head = " ".join(text.split(",")[0].split())
    return _DISTRICT_SUFFIX.sub("", head).strip()


def place_key(text: str | None) -> str:
    """Llave de agrupación de una ciudad o un barrio escrito a mano: sin
    tildes, mayúsculas ni puntuación. "" = no hay lugar."""
    decomposed = unicodedata.normalize("NFKD", _head(text))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_NOT_A_WORD.sub(" ", plain.casefold()).split())


def _is_well_written(text: str) -> bool:
    return text != text.lower() and text != text.upper()


def _label(spellings: Counter[str]) -> str:
    """La forma más escrita; a igualdad, la que tiene mayúsculas y tildes.
    Si todas están en minúsculas (o mayúsculas), se capitaliza."""
    best = max(
        spellings,
        key=lambda t: (spellings[t], _is_well_written(t), t != place_key(t)),
    )
    if _is_well_written(best):
        return best
    return " ".join(w[:1].upper() + w[1:].lower() for w in best.split())


def order_campaign_links(
    sessions: list[tuple[Any, dict[str, Any]]],
) -> dict[str, CampaignLink]:
    """`order_id → campaña` de cada pedido que registró una conversación.

    Misma atribución por episodio que el resto de Ads: referral del anuncio >
    campaña de WhatsApp en ventana > directo > origen de la sesión. Las
    sesiones viejas sin `episodes[]` aportan su `registered_order`.
    """
    links: dict[str, CampaignLink] = {}
    for _session_dir, metadata in sessions:
        origin = metadata.get("origin") or {}
        touches = metadata.get("campaign_touches")
        episodes = [ep for ep in metadata.get("episodes") or [] if isinstance(ep, dict)]
        if episodes:
            pairs = [(ep, ep.get("order_id"), ep.get("started_at_ms")) for ep in episodes]
        else:
            reg = metadata.get("registered_order")
            oid = reg.get("order_id") if isinstance(reg, dict) else None
            pairs = [(None, oid, origin.get("first_seen_ms"))]
        for ep, order_id, started in pairs:
            if not isinstance(order_id, str) or not order_id or order_id in links:
                continue
            touch = _attributed_campaign_touch(ep, touches, started)
            campaign_id = _episode_to_campaign_id(ep, origin, touch)
            if campaign_id is None:
                continue
            links[order_id] = CampaignLink(
                id=campaign_id, name=_bucket_name(campaign_id, ep, origin, touch)
            )
    return links


def _bucket_name(
    campaign_id: str,
    episode: dict[str, Any] | None,
    origin: dict[str, Any],
    touch: dict[str, Any] | None,
) -> str | None:
    if campaign_id == DIRECT_CAMPAIGN_ID:
        return DIRECT_CAMPAIGN_NAME
    if campaign_id == WEB_CART_CAMPAIGN_ID:
        return WEB_CART_CAMPAIGN_NAME
    return _episode_headline(episode, origin, touch)


def _is_purchase(order: Any) -> bool:
    return getattr(order, "status", None) != "cancelled" and not getattr(order, "is_test", False)


def _clean(text: Any) -> str | None:
    return text.strip() or None if isinstance(text, str) else None


def purchase_rows(
    orders: list[Any],
    links: dict[str, CampaignLink],
    names: dict[str, dict[str, str | None]],
) -> list[PurchaseRow]:
    """Las compras de `orders` (`OrderSummaryDTO`) con su campaña.

    Un anuncio que el resolver de Meta conoce se agrupa bajo su campaña (como
    la lista de Ads) y conserva el anuncio en `source_id`; sin conversación →
    `UNLINKED_CAMPAIGN_ID`.
    """
    rows: list[PurchaseRow] = []
    for order in orders:
        if not _is_purchase(order):
            continue
        link = links.get(order.id)
        if link is None:
            campaign_id, campaign_name, source_id = UNLINKED_CAMPAIGN_ID, UNLINKED_CAMPAIGN_NAME, None
        else:
            info = names.get(link.id) or {}
            campaign_id = info.get("campaign_id") or link.id
            campaign_name = info.get("campaign_name") or link.name
            source_id = link.id
        rows.append(
            PurchaseRow(
                order_id=order.id,
                city=_clean(order.city),
                neighborhood=_clean(getattr(order, "neighborhood", None)),
                value_cop=int(order.total_cop or 0),
                paid=order.pay_status == "paid",
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                source_id=source_id,
            )
        )
    return rows


def rows_in_scope(
    rows: list[PurchaseRow],
    *,
    campaign_id: str,
    source_ids: frozenset[str] | None = None,
) -> list[PurchaseRow]:
    """Las compras de una fila de Ads: la campaña agrupada, un anuncio suelto
    (id crudo del vault) o, con `source_ids`, los anuncios de un segmento."""
    if source_ids is not None:
        return [r for r in rows if r.source_id in source_ids]
    return [r for r in rows if campaign_id in (r.campaign_id, r.source_id)]


@dataclass
class _Tally:
    orders: int = 0
    paid_orders: int = 0
    value_cop: int = 0
    spellings: Counter[str] = field(default_factory=Counter)

    def add(self, row: PurchaseRow, spelling: str) -> None:
        self.orders += 1
        self.paid_orders += int(row.paid)
        self.value_cop += row.value_cop
        if spelling:
            self.spellings[spelling] += 1


def _ranked(tallies: dict[Any, _Tally]) -> list[tuple[Any, _Tally]]:
    return sorted(tallies.items(), key=lambda kv: (-kv[1].orders, -kv[1].value_cop, str(kv[0])))


def summarize_purchase_places(
    rows: list[PurchaseRow], *, overall: PurchasePlaces | None = None
) -> PurchasePlaces:
    """Compras por ciudad (las de más compras primero) y, dentro de cada una,
    por zona y por campaña. Con `overall` (el resumen de TODAS las compras),
    cada ciudad trae cuántas compras hubo ahí en total: el cotejo."""
    total = _Tally()
    cities: dict[str, _Tally] = {}
    zones: dict[str, dict[str, _Tally]] = {}
    campaigns: dict[str, dict[str, _Tally]] = {}
    campaign_names: dict[str, str | None] = {}
    without_city = 0
    for row in rows:
        total.add(row, "")
        key = place_key(row.city)
        if not key:
            without_city += 1
            continue
        cities.setdefault(key, _Tally()).add(row, _head(row.city))
        zone_key = place_key(row.neighborhood)
        zones.setdefault(key, {}).setdefault(zone_key, _Tally()).add(row, _head(row.neighborhood))
        campaigns.setdefault(key, {}).setdefault(row.campaign_id, _Tally()).add(row, "")
        campaign_names.setdefault(row.campaign_id, row.campaign_name)

    overall_by_key = {c.key: c.orders for c in overall.cities} if overall is not None else None
    return PurchasePlaces(
        orders=total.orders,
        paid_orders=total.paid_orders,
        value_cop=total.value_cop,
        without_city=without_city,
        cities=[
            CityPurchases(
                key=key,
                city=_label(tally.spellings),
                orders=tally.orders,
                paid_orders=tally.paid_orders,
                value_cop=tally.value_cop,
                zones=[
                    ZonePurchases(
                        zone=_label(z.spellings) if zone_key else None,
                        orders=z.orders,
                        value_cop=z.value_cop,
                    )
                    for zone_key, z in _ranked(zones[key])
                ],
                campaigns=[
                    CampaignPurchases(
                        id=cid, name=campaign_names.get(cid), orders=c.orders, value_cop=c.value_cop
                    )
                    for cid, c in _ranked(campaigns[key])
                ],
                overall_orders=overall_by_key.get(key, 0) if overall_by_key is not None else None,
            )
            for key, tally in _ranked(cities)
        ],
        overall_orders=overall.orders if overall is not None else None,
    )


async def collect_orders_in_window(
    port: Any,
    *,
    since_ms: int | None,
    until_ms: int | None,
    page_size: int = 100,
    max_pages: int = 20,
) -> OrdersInWindow:
    """Los pedidos de Orders creados en `[since_ms, until_ms)`.

    El listado viene del más nuevo al más viejo, con pedidos y borradores
    paginados por separado: se sigue pidiendo mientras alguno de los dos
    llenó la página y todavía no salió de la ventana. Más de `max_pages`
    páginas → `truncated`. Orders caído → `available=False`, sin pedidos.
    """
    found: dict[str, Any] = {}
    for page_no in range(max_pages):
        page = await port.list(limit=page_size, offset=page_no * page_size, include_drafts=True)
        if not page.catalog_available:
            return OrdersInWindow(orders=[], available=False)
        for order in page.orders:
            at = order.created_at_ms
            if (since_ms is None or at >= since_ms) and (until_ms is None or at < until_ms):
                found.setdefault(order.id, order)
        if not any(
            len(batch) >= page_size and (since_ms is None or min(o.created_at_ms for o in batch) > since_ms)
            for batch in (
                [o for o in page.orders if not o.is_draft],
                [o for o in page.orders if o.is_draft],
            )
        ):
            return OrdersInWindow(orders=list(found.values()))
    return OrdersInWindow(orders=list(found.values()), truncated=True)
