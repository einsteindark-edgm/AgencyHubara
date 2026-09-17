"""Revenue de Ads = el valor del pedido que muestra Orders (OrderFacts).

Pedido #31 (2026-09-17): se editó el producto (y el total) en Medusa. Orders
mostró el total nuevo y Ads el viejo, porque Ads sumaba la copia congelada en
el chat. Ahora el vault solo aporta el VÍNCULO episodio→pedido y el valor sale
de `OrderFacts`:

  * pedido pagado y no cancelado → su total vivo;
  * pendiente / reembolsado / cancelado / inexistente → no suma;
  * Medusa no responde → total congelado del chat + aviso `orders_stale`.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.ads.api as ads_mod
from src.plugins.ads.aggregation import (
    list_ads_campaigns,
    list_attributed_conversations,
    session_order_ids,
)
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts, OrderFactsSnapshot


def _fact(order_id: str, total: int, *, pay: str = "paid", stage: str = "preparing") -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id="#31", total_cop=total, currency_code="cop",
        pay_status=pay, stage=stage, customer="Ana", is_draft=False,
    )


def _ep(episode_id: str, started: int, order_id: str | None, frozen: int | None) -> dict:
    return {
        "episode_id": episode_id,
        "started_at_ms": started,
        "closed_at_ms": started + 1,
        "closing_tag": "COMPRA_EXITOSA" if order_id else None,
        "order_id": order_id,
        "order_total_cop": frozen,
        "referral_snapshot": None,
    }


def _session(vault: Path, phone: str, episodes: list[dict], registered: dict | None = None) -> None:
    d = vault / f"wa_{phone}"
    d.mkdir(parents=True, exist_ok=True)
    md: dict = {
        "origin": {"channel": "ad", "first_seen_ms": 1, "headline": "Velas", "source_id": "AD_X"},
        "active_route": "ventas",
        "episodes": episodes,
    }
    if registered is not None:
        md["registered_order"] = registered
    (d / "metadata.json").write_text(json.dumps(md), encoding="utf-8")


def _camp(vault: Path, facts: OrderFactsSnapshot):
    return next(c for c in list_ads_campaigns(vault, order_facts=facts) if c.id == "AD_X")


def test_edited_order_uses_the_live_total(tmp_path: Path) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", 90000)])
    facts = OrderFactsSnapshot(facts={"order_31": _fact("order_31", 120000)})
    camp = _camp(tmp_path, facts)
    assert camp.revenue == 120000
    assert camp.avg_ticket == 120000


@pytest.mark.parametrize(
    "fact",
    [
        _fact("order_31", 120000, pay="pending"),
        _fact("order_31", 120000, pay="refund"),
        _fact("order_31", 120000, stage="cancelled"),
    ],
)
def test_orders_not_paid_do_not_add_revenue(tmp_path: Path, fact: OrderFacts) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", 90000)])
    camp = _camp(tmp_path, OrderFactsSnapshot(facts={"order_31": fact}))
    assert camp.revenue is None
    assert camp.revenue_count == 0


def test_order_missing_in_medusa_does_not_add_revenue(tmp_path: Path) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", 90000)])
    assert _camp(tmp_path, OrderFactsSnapshot()).revenue is None


def test_medusa_down_falls_back_to_frozen_total(tmp_path: Path) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", None)],
             registered={"success": True, "order_id": "order_31", "total_cop": 80000})
    facts = OrderFactsSnapshot(unresolved=frozenset({"order_31"}), stale=True)
    assert _camp(tmp_path, facts).revenue == 80000


def test_legacy_session_without_episodes_reads_facts(tmp_path: Path) -> None:
    _session(tmp_path, "31", [], registered={"success": True, "order_id": "order_31", "total_cop": 80000})
    facts = OrderFactsSnapshot(facts={"order_31": _fact("order_31", 95000)})
    assert _camp(tmp_path, facts).revenue == 95000


def test_conversation_value_uses_the_live_total(tmp_path: Path) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", 90000)])
    facts = OrderFactsSnapshot(facts={"order_31": _fact("order_31", 120000)})
    convs = list_attributed_conversations(tmp_path, "AD_X", order_facts=facts)
    assert [c.value for c in convs] == [120000]


def test_session_order_ids_collects_episodes_and_registered_order(tmp_path: Path) -> None:
    _session(tmp_path, "31", [_ep("ep1", 10, "order_31", 1), _ep("ep2", 20, None, None)],
             registered={"success": True, "order_id": "order_30"})
    sessions = [(tmp_path / "wa_31", json.loads((tmp_path / "wa_31" / "metadata.json").read_text()))]
    assert session_order_ids(sessions) == {"order_31", "order_30"}


# --- endpoints -------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    ads_mod._scan_cache.clear()
    facts = InMemoryOrderFacts([_fact("order_31", 120000)])
    with patch.object(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path), patch.object(
        ads_mod, "get_order_facts_port", lambda: facts
    ), patch.object(ads_mod, "_cached_meta_names", lambda ids: {}), patch.object(
        ads_mod, "_cached_meta_campaigns", lambda s, u: ([], [])
    ):
        app = FastAPI()
        app.include_router(ads_mod.router, prefix="/api/ads")
        yield TestClient(app), tmp_path, facts
    ads_mod._scan_cache.clear()


def test_campaigns_endpoint_reads_order_facts(client) -> None:
    http, vault, _facts = client
    _session(vault, "31", [_ep("ep1", 10, "order_31", 90000)])
    body = http.get("/api/ads/campaigns").json()
    camp = next(c for c in body["campaigns"] if c["id"] == "AD_X")
    assert camp["revenue"] == 120000
    assert body["orders_stale"] is False


def test_campaigns_endpoint_flags_stale_when_medusa_is_down(client) -> None:
    http, vault, facts = client
    facts.available = False
    _session(vault, "31", [_ep("ep1", 10, "order_31", 90000)])
    body = http.get("/api/ads/campaigns").json()
    camp = next(c for c in body["campaigns"] if c["id"] == "AD_X")
    assert camp["revenue"] == 90000
    assert body["orders_stale"] is True


def test_conversations_endpoint_reads_order_facts(client) -> None:
    http, vault, _facts = client
    _session(vault, "31", [_ep("ep1", 10, "order_31", 90000)])
    body = http.get("/api/ads/campaigns/AD_X/conversations").json()
    assert [c["value"] for c in body["conversations"]] == [120000]
    assert body["orders_stale"] is False
