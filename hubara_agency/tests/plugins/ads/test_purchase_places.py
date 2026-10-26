"""Dónde compran: las compras por ciudad y zona, por campaña y en total.

Pedido del operador (2026-10-23): «saber la ciudad y zonas donde están
comprando por campaña, en cada campaña poder cotejar esos datos, y una vista
general de todas las compras».

La ciudad y el barrio salen del pedido en Orders (gotcha 13); la campaña, del
vínculo conversación→pedido del vault (la misma atribución del resto de Ads).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.plugins.ads.aggregation import (
    DIRECT_CAMPAIGN_ID,
    DIRECT_CAMPAIGN_NAME,
    list_attributed_conversations,
)
from src.plugins.ads.purchase_places import (
    UNLINKED_CAMPAIGN_ID,
    UNLINKED_CAMPAIGN_NAME,
    CampaignLink,
    PurchaseRow,
    collect_orders_in_window,
    order_campaign_links,
    place_key,
    purchase_rows,
    rows_in_scope,
    summarize_purchase_places,
)
from src.sdk.connectorkit import OrderFacts, OrderFactsSnapshot
from tests.platform.orders.fakes import FakeQuery
from tests.platform.orders.fakes import order_summary as _summary

_T0 = 1_750_000_000_000
_DAY_MS = 24 * 60 * 60 * 1000


def _seed(vault: Path, phone: str, metadata: dict) -> tuple[Path, dict]:
    d = vault / f"wa_{phone}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return d, metadata


def _ep(episode_id: str, order_id: str | None, snap: dict | None, started: int = _T0) -> dict:
    return {
        "episode_id": episode_id,
        "started_at_ms": started,
        "closed_at_ms": started + 1,
        "closing_tag": "COMPRA_EXITOSA" if order_id else None,
        "order_id": order_id,
        "referral_snapshot": snap,
    }


_AD_X = {"channel": "ad", "source_id": "AD_X", "headline": "Velitas"}
_ORIGIN_AD = {"channel": "ad", "first_seen_ms": 1, "headline": "Velitas", "source_id": "AD_X"}


def _order(order_id: str, *, city: str | None, zone: str | None, total: int = 50_000, **kw):
    return replace(
        _summary(order_id, total, pay=kw.pop("pay", "paid"), stage=kw.pop("stage", "preparing")),
        city=city,
        neighborhood=zone,
        created_at_ms=kw.pop("created_at_ms", _T0),
        **kw,
    )


def _row(order_id: str, city: str | None, zone: str | None = None, *, campaign: str = "C1",
         value: int = 10_000, paid: bool = True, source_id: str | None = None) -> PurchaseRow:
    return PurchaseRow(
        order_id=order_id, city=city, neighborhood=zone, value_cop=value, paid=paid,
        campaign_id=campaign, campaign_name=f"Campaña {campaign}", source_id=source_id or campaign,
    )


# --- la conversación muestra dónde se entrega su pedido ---------------------


def test_conversation_shows_city_and_neighborhood_of_its_order(tmp_path: Path) -> None:
    _seed(tmp_path, "31", {"origin": _ORIGIN_AD, "episodes": [
        _ep("ep1", "order_31", _AD_X), _ep("ep2", None, _AD_X, started=_T0 + 10),
    ]})
    fact = OrderFacts(
        order_id="order_31", display_id="#31", total_cop=50_000, currency_code="cop",
        pay_status="paid", stage="preparing", customer="Ana", is_draft=False,
        city="Medellín", neighborhood="Laureles",
    )
    convs = list_attributed_conversations(
        tmp_path, "AD_X", order_facts=OrderFactsSnapshot(facts={"order_31": fact})
    )
    by_ep = {c.episode_id: (c.city, c.neighborhood) for c in convs}
    assert by_ep == {"ep1": ("Medellín", "Laureles"), "ep2": (None, None)}


# --- llave de agrupación ----------------------------------------------------


@pytest.mark.parametrize(
    "spelling", ["Bogotá", "bogota", "BOGOTA D.C.", "Bogotá, D.C.", "  Bogotá  dc ", "Bogota DC"]
)
def test_spellings_of_the_same_city_share_one_key(spelling: str) -> None:
    assert place_key(spelling) == "bogota"


def test_city_followed_by_its_department_is_the_city() -> None:
    assert place_key("Chía, Cundinamarca") == place_key("chia")


def test_missing_place_has_an_empty_key() -> None:
    assert place_key(None) == place_key("  ") == ""


# --- qué campaña trajo cada pedido -------------------------------------------


def test_links_each_order_to_the_campaign_of_its_conversation(tmp_path: Path) -> None:
    touch = {"campaign_id": "mkt-abc", "campaign_name": "Promo velitas", "sent_at_ms": _T0 - 3_600_000}
    sessions = [
        _seed(tmp_path, "1", {"origin": _ORIGIN_AD, "episodes": [_ep("ep1", "order_ad", _AD_X)]}),
        _seed(tmp_path, "2", {
            "origin": {"channel": "direct", "first_seen_ms": 1},
            "campaign_touches": [touch],
            "episodes": [_ep("ep1", "order_mkt", {"channel": "direct"})],
        }),
        _seed(tmp_path, "3", {
            "origin": {"channel": "direct", "first_seen_ms": 1},
            "episodes": [_ep("ep1", "order_direct", {"channel": "direct"})],
        }),
        # Sesión legacy sin episodes[]: el pedido sale de `registered_order`.
        _seed(tmp_path, "4", {"origin": _ORIGIN_AD, "registered_order": {"order_id": "order_legacy"}}),
    ]

    assert order_campaign_links(sessions) == {
        "order_ad": CampaignLink(id="AD_X", name="Velitas"),
        "order_mkt": CampaignLink(id="mkt-abc", name="Promo velitas"),
        "order_direct": CampaignLink(id=DIRECT_CAMPAIGN_ID, name=DIRECT_CAMPAIGN_NAME),
        "order_legacy": CampaignLink(id="AD_X", name="Velitas"),
    }


# --- filas de compra ---------------------------------------------------------


def test_purchases_are_registered_orders_not_cancelled_nor_test() -> None:
    orders = [
        _order("o_paid", city="Bogotá", zone="Chapinero"),
        _order("o_cod", city="Cali", zone=None, pay="pending", is_draft=True),
        _order("o_cancel", city="Bogotá", zone="Suba", stage="cancelled"),
        _order("o_test", city="Bogotá", zone="Suba", is_test=True),
    ]
    rows = purchase_rows(orders, {}, {})
    assert [(r.order_id, r.paid) for r in rows] == [("o_paid", True), ("o_cod", False)]


def test_rows_group_ads_under_their_meta_campaign_and_keep_the_ad() -> None:
    orders = [_order("o1", city="Bogotá", zone="Chapinero"), _order("o2", city="Cali", zone="Granada")]
    links = {"o1": CampaignLink(id="AD_X", name="Velitas")}
    names = {"AD_X": {"campaign_id": "CMP_1", "campaign_name": "Velitas diciembre", "adset_id": "AS_1"}}

    rows = {r.order_id: r for r in purchase_rows(orders, links, names)}

    assert (rows["o1"].campaign_id, rows["o1"].campaign_name, rows["o1"].source_id) == (
        "CMP_1", "Velitas diciembre", "AD_X"
    )
    assert (rows["o2"].campaign_id, rows["o2"].campaign_name, rows["o2"].source_id) == (
        UNLINKED_CAMPAIGN_ID, UNLINKED_CAMPAIGN_NAME, None
    )
    assert (rows["o1"].city, rows["o1"].neighborhood, rows["o1"].value_cop) == ("Bogotá", "Chapinero", 50_000)


def test_scope_keeps_one_campaign_or_its_ads() -> None:
    rows = [
        _row("a", "Bogotá", campaign="CMP_1", source_id="AD_1"),
        _row("b", "Bogotá", campaign="CMP_1", source_id="AD_2"),
        _row("c", "Cali", campaign="direct", source_id="direct"),
    ]
    assert [r.order_id for r in rows_in_scope(rows, campaign_id="CMP_1")] == ["a", "b"]
    # Un anuncio suelto (id crudo del vault) también es un scope.
    assert [r.order_id for r in rows_in_scope(rows, campaign_id="AD_2")] == ["b"]
    # Segmento: los anuncios que el resolver pone en él.
    assert [r.order_id for r in rows_in_scope(rows, campaign_id="CMP_1", source_ids=frozenset({"AD_1"}))] == ["a"]


# --- resumen por ciudad y zona ------------------------------------------------


def test_summary_counts_purchases_by_city_then_zone() -> None:
    rows = [
        _row("1", "Bogotá", "Chapinero", value=10_000),
        _row("2", "bogota", "chapinero", value=20_000, paid=False),
        _row("3", "Bogotá D.C.", "Suba", value=30_000),
        _row("4", "Medellín", "Laureles", value=90_000),
        _row("5", None, None, value=5_000),
    ]
    summary = summarize_purchase_places(rows)

    assert (summary.orders, summary.paid_orders, summary.value_cop, summary.without_city) == (
        5, 4, 155_000, 1
    )
    bogota, medellin = summary.cities
    # Más compras primero; la forma más escrita da el nombre.
    assert (bogota.city, bogota.orders, bogota.paid_orders, bogota.value_cop) == ("Bogotá", 3, 2, 60_000)
    assert [(z.zone, z.orders, z.value_cop) for z in bogota.zones] == [
        ("Chapinero", 2, 30_000), ("Suba", 1, 30_000)
    ]
    assert (medellin.city, medellin.orders) == ("Medellín", 1)


def test_order_without_neighborhood_is_its_own_zone() -> None:
    summary = summarize_purchase_places([_row("1", "Cali", None), _row("2", "Cali", "Granada")])
    assert {(z.zone, z.orders) for z in summary.cities[0].zones} == {(None, 1), ("Granada", 1)}


def test_lowercase_only_spellings_are_shown_capitalised() -> None:
    summary = summarize_purchase_places([_row("1", "medellín", "el poblado")])
    city = summary.cities[0]
    assert (city.city, city.zones[0].zone) == ("Medellín", "El Poblado")


def test_each_city_says_which_campaigns_brought_its_purchases() -> None:
    rows = [
        _row("1", "Bogotá", campaign="C1", value=10_000),
        _row("2", "Bogotá", campaign="C2", value=10_000),
        _row("3", "Bogotá", campaign="C2", value=10_000),
    ]
    city = summarize_purchase_places(rows).cities[0]
    assert [(c.id, c.name, c.orders, c.value_cop) for c in city.campaigns] == [
        ("C2", "Campaña C2", 2, 20_000), ("C1", "Campaña C1", 1, 10_000)
    ]


def test_a_campaign_is_compared_with_all_purchases() -> None:
    every = [_row("1", "Bogotá", campaign="C1"), _row("2", "Bogotá", campaign="C2"),
             _row("3", "Cali", campaign="C2"), _row("4", "Cali", campaign="C2")]
    overall = summarize_purchase_places(every)

    mine = summarize_purchase_places(rows_in_scope(every, campaign_id="C1"), overall=overall)

    assert mine.overall_orders == 4
    assert [(c.city, c.orders, c.overall_orders) for c in mine.cities] == [("Bogotá", 1, 2)]


# --- los pedidos de la ventana -------------------------------------------------


@pytest.mark.asyncio
async def test_collects_only_orders_created_in_the_window() -> None:
    orders = [_order(f"o{i}", city="Cali", zone=None, created_at_ms=_T0 - i * _DAY_MS) for i in range(10)]
    port = FakeQuery(orders)

    got = await collect_orders_in_window(
        port, since_ms=_T0 - 3 * _DAY_MS, until_ms=_T0, page_size=2
    )

    assert [o.id for o in got.orders] == ["o1", "o2", "o3"]
    assert (got.available, got.truncated) == (True, False)
    # Para de paginar cuando la página ya es más vieja que la ventana.
    assert port.list_calls == [0, 2]


@pytest.mark.asyncio
async def test_page_cap_is_reported_as_truncated() -> None:
    orders = [_order(f"o{i}", city="Cali", zone=None, created_at_ms=_T0 - i) for i in range(10)]
    got = await collect_orders_in_window(
        FakeQuery(orders), since_ms=None, until_ms=None, page_size=2, max_pages=3
    )
    assert len(got.orders) == 6
    assert got.truncated is True


@pytest.mark.asyncio
async def test_orders_down_is_reported_as_unavailable() -> None:
    got = await collect_orders_in_window(FakeQuery([], available=False), since_ms=None, until_ms=None)
    assert (got.orders, got.available) == ([], False)


# --- endpoint: vista general y por campaña ------------------------------------


@pytest.fixture
def places_client(tmp_path: Path):
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.plugins.ads.api as ads_mod

    names = {"AD_X": {"campaign_id": "CMP_1", "campaign_name": "Velitas diciembre", "adset_id": "AS_1"}}
    port = FakeQuery([])
    ads_mod._scan_cache.clear()
    ads_mod._places_cache.clear()
    with patch.object(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path), patch.object(
        ads_mod, "_cached_meta_names", lambda ids: {i: names[i] for i in ids if i in names}
    ), patch.object(ads_mod, "get_order_query_port", lambda: port):
        app = FastAPI()
        app.include_router(ads_mod.router, prefix="/api/ads")
        yield TestClient(app), tmp_path, port
    ads_mod._scan_cache.clear()
    ads_mod._places_cache.clear()


def _shop(vault: Path, port: FakeQuery, *, created_at_ms: int = _T0) -> None:
    """Una compra por anuncio (Bogotá/Chapinero), una directa (Cali) y una de
    la tienda web sin conversación (Bogotá/Suba)."""
    _seed(vault, "1", {"origin": _ORIGIN_AD, "episodes": [_ep("ep1", "o_ad", _AD_X)]})
    _seed(vault, "2", {"origin": {"channel": "direct", "first_seen_ms": 1},
                       "episodes": [_ep("ep1", "o_direct", {"channel": "direct"})]})
    port.orders = [
        _order("o_ad", city="Bogotá", zone="Chapinero", total=60_000, created_at_ms=created_at_ms),
        _order("o_direct", city="Cali", zone="Granada", total=40_000, created_at_ms=created_at_ms - 1),
        _order("o_web", city="bogota", zone="Suba", total=30_000, created_at_ms=created_at_ms - 2),
    ]


def test_general_view_counts_every_purchase_with_its_campaign(places_client) -> None:
    http, vault, port = places_client
    _shop(vault, port)

    body = http.get("/api/ads/purchase-places").json()

    assert (body["orders"], body["value_cop"], body["orders_available"], body["truncated"]) == (
        3, 130_000, True, False
    )
    bogota, cali = body["cities"]
    assert (bogota["city"], bogota["orders"]) == ("Bogotá", 2)
    assert {z["zone"] for z in bogota["zones"]} == {"Chapinero", "Suba"}
    assert {(c["id"], c["name"]) for c in bogota["campaigns"]} == {
        ("CMP_1", "Velitas diciembre"), (UNLINKED_CAMPAIGN_ID, UNLINKED_CAMPAIGN_NAME)
    }
    assert [c["id"] for c in cali["campaigns"]] == [DIRECT_CAMPAIGN_ID]


def test_campaign_view_is_compared_with_all_purchases(places_client) -> None:
    http, vault, port = places_client
    _shop(vault, port)

    body = http.get("/api/ads/purchase-places", params={"campaign_id": "CMP_1"}).json()

    assert (body["orders"], body["overall_orders"]) == (1, 3)
    assert [(c["city"], c["orders"], c["overall_orders"]) for c in body["cities"]] == [("Bogotá", 1, 2)]
    # El segmento del anuncio también es un scope.
    seg = http.get("/api/ads/purchase-places", params={"campaign_id": "CMP_1", "adset_id": "AS_1"}).json()
    assert seg["orders"] == 1


def test_window_uses_the_date_of_the_purchase(places_client) -> None:
    from src.plugins.ads.aggregation import bogota_day_start_ms

    http, vault, port = places_client
    _shop(vault, port, created_at_ms=bogota_day_start_ms("2026-10-20") + 3_600_000)

    inside = http.get("/api/ads/purchase-places", params={"from": "2026-10-20", "to": "2026-10-20"}).json()
    outside = http.get("/api/ads/purchase-places", params={"from": "2026-10-21", "to": "2026-10-22"}).json()

    assert (inside["orders"], outside["orders"]) == (3, 0)


def test_orders_down_is_said_not_shown_as_zero(places_client) -> None:
    http, vault, port = places_client
    _shop(vault, port)
    port.available = False

    body = http.get("/api/ads/purchase-places").json()

    assert (body["orders_available"], body["cities"]) == (False, [])
