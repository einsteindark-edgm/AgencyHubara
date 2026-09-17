"""Ventas atribuidas a una campaña = el valor del pedido en Orders (OrderFacts).

Mismo bug que Ads en el pedido #31: `campaign_stats` sumaba
`episode.order_total_cop` (copia congelada). Ahora cuenta solo pedidos
pagados y no cancelados con su total vivo; si Medusa no responde usa la copia
y lo avisa con `orders_stale`.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.marketing.api as api_mod
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaigns import campaign_stats
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts, OrderFactsSnapshot

T0 = 1_750_000_000_000
HOUR = 60 * 60 * 1000


def _fact(order_id: str, total: int, *, pay: str = "paid") -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id="#1", total_cop=total, currency_code="cop",
        pay_status=pay, stage="preparing", customer="Ana", is_draft=False,
    )


def _metadata(campaign_id: str, orders: list[tuple[str, int]]) -> dict:
    return {
        "campaign_touches": [{"campaign_id": campaign_id, "campaign_name": "Promo", "sent_at_ms": T0}],
        "last_inbound_at_ms": T0 + HOUR,
        "episodes": [
            {"episode_id": f"ep_{i}", "started_at_ms": T0 + HOUR, "order_id": oid, "order_total_cop": frozen}
            for i, (oid, frozen) in enumerate(orders)
        ],
    }


def test_stats_use_live_total_and_only_paid_orders() -> None:
    sessions = [("wa_1", _metadata("c1", [("OB-1", 44000), ("OB-2", 30000)]))]
    facts = OrderFactsSnapshot(facts={
        "OB-1": _fact("OB-1", 60000),
        "OB-2": _fact("OB-2", 30000, pay="pending"),
    })
    stats = campaign_stats({"id": "c1"}, sessions, order_facts=facts)
    assert stats["attributed_orders"] == 1
    assert stats["attributed_revenue_cop"] == 60000


def test_stats_fall_back_to_frozen_when_medusa_is_down() -> None:
    sessions = [("wa_1", _metadata("c1", [("OB-1", 44000)]))]
    facts = OrderFactsSnapshot(unresolved=frozenset({"OB-1"}), stale=True)
    stats = campaign_stats({"id": "c1"}, sessions, order_facts=facts)
    assert stats["attributed_revenue_cop"] == 44000


def test_stats_endpoint_reads_order_facts(_isolate_vault_dir: Path, monkeypatch) -> None:
    facts = InMemoryOrderFacts([_fact("OB-1", 60000)])
    monkeypatch.setattr(api_mod, "get_order_facts_port", lambda: facts)
    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/marketing")
    client = TestClient(app)

    campaign_id = client.post("/api/marketing/campaigns", json={"name": "Promo"}).json()["id"]
    store = CampaignStore(_isolate_vault_dir)
    campaign = store.get(campaign_id)
    campaign["status"] = "sent"
    campaign["sent_at_ms"] = T0
    store.save(campaign)
    session_dir = _isolate_vault_dir / "wa_+571"
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(_metadata(campaign_id, [("OB-1", 44000)])), encoding="utf-8"
    )

    stats = client.get(f"/api/marketing/campaigns/{campaign_id}/stats").json()
    assert stats["attributed_revenue_cop"] == 60000
    assert stats["orders_stale"] is False

    facts.available = False
    stats = client.get(f"/api/marketing/campaigns/{campaign_id}/stats").json()
    assert stats["attributed_revenue_cop"] == 44000
    assert stats["orders_stale"] is True
