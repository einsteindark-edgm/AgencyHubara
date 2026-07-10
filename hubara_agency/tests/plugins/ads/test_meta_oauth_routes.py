"""Rutas PROTEGIDAS `/api/ads/meta/*` — status, insights, gestión.

(Single-tenant: no hay login/callback/disconnect — el token se provisiona en SSM;
los guards de esa superficie viven en test_meta_single_tenant.py.)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.ads.api import meta_oauth
from src.plugins.ads.meta.client import FakeMetaAds, MetaAdAccount, MetaCampaignMeta
from src.plugins.ads.meta.parse import MetaCampaignMetrics
from src.plugins.ads.meta.settings import MetaSettings
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken

_SETTINGS = MetaSettings(tenant="hubara", region=None)


def _client(monkeypatch, *, store=None, ads=None, settings=_SETTINGS) -> TestClient:
    store = store or InMemoryTokenStore()
    ads = ads or FakeMetaAds()
    monkeypatch.setattr(meta_oauth, "_settings", lambda: settings)
    monkeypatch.setattr(meta_oauth, "_store", lambda: store)
    monkeypatch.setattr(meta_oauth, "_ads", lambda: ads)
    app = FastAPI()
    app.include_router(meta_oauth.router, prefix="/api/ads/meta")
    return TestClient(app)


def _connected_store(scopes=("ads_read",), expires_at=1782842400) -> InMemoryTokenStore:
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", expires_at, scopes, "act_1010393601284112", "Hubara"))
    return store


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reports_disconnected_when_no_token(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/api/ads/meta/status").json() == {"connected": False}


def test_status_reports_connected_with_account(monkeypatch) -> None:
    client = _client(monkeypatch, store=_connected_store())
    body = client.get("/api/ads/meta/status").json()
    assert body["connected"] is True
    assert body["account_name"] == "Hubara"
    assert body["can_manage"] is False  # solo ads_read


def test_status_flags_expired_and_can_manage(monkeypatch) -> None:
    # token expirado (expires_at en el pasado) + scope de gestión.
    store = _connected_store(scopes=("ads_read", "ads_management"), expires_at=1)
    body = _client(monkeypatch, store=store).get("/api/ads/meta/status").json()
    assert body["expired"] is True
    assert body["can_manage"] is True


# ── insights ─────────────────────────────────────────────────────────────────

def test_insights_returns_disconnected_when_no_token(monkeypatch) -> None:
    body = _client(monkeypatch).get("/api/ads/meta/insights").json()
    assert body == {"connected": False, "campaigns": []}


def test_insights_default_window_derives_since_until(monkeypatch) -> None:
    # Caso DEFAULT (sin since/until — lo que manda el frontend con days=30). Antes
    # tiraba NameError por `date`/`timedelta` no importados (premortem #1).
    ads = FakeMetaAds(
        metrics=[MetaCampaignMetrics("c1", "Duo", 100.0, 10, 8, 5, 2)],
        campaigns=[MetaCampaignMeta("c1", "Duo", "ACTIVE", "OUTCOME_SALES")],
    )
    body = _client(monkeypatch, store=_connected_store(), ads=ads).get(
        "/api/ads/meta/insights?days=30"
    ).json()
    assert body["connected"] is True
    assert body["since"] and body["until"]  # derivados, no None
    assert body["campaigns"][0]["spend"] == 100.0


def test_insights_merges_metrics_with_status_and_objective(monkeypatch) -> None:
    ads = FakeMetaAds(
        metrics=[MetaCampaignMetrics("c1", "Duo zodiacal", 896823.0, 45000, 38000, 571, 205)],
        campaigns=[MetaCampaignMeta("c1", "Duo zodiacal", "ACTIVE", "OUTCOME_SALES")],
    )
    body = _client(monkeypatch, store=_connected_store(), ads=ads).get(
        "/api/ads/meta/insights?since=2026-06-01&until=2026-06-30"
    ).json()
    row = body["campaigns"][0]
    assert row["spend"] == 896823.0
    assert row["clicks"] == 571
    assert row["messaging_conversations_started"] == 205
    assert row["status"] == "ACTIVE"
    assert row["objective"] == "OUTCOME_SALES"


# ── gestión (writes) ─────────────────────────────────────────────────────────

def test_set_campaign_status_pauses_via_port(monkeypatch) -> None:
    store = _connected_store(scopes=("ads_read", "ads_management"))
    ads = FakeMetaAds()
    client = _client(monkeypatch, store=store, ads=ads)
    resp = client.post("/api/ads/meta/campaigns/c1/status", json={"status": "PAUSED"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "campaign_id": "c1", "status": "PAUSED"}
    assert ads.status_changes == [("c1", "PAUSED")]


def test_set_campaign_status_rejects_invalid_status(monkeypatch) -> None:
    store = _connected_store(scopes=("ads_management",))
    resp = _client(monkeypatch, store=store).post(
        "/api/ads/meta/campaigns/c1/status", json={"status": "DELETED"}
    )
    assert resp.status_code == 400


def test_set_campaign_status_requires_connection(monkeypatch) -> None:
    ads = FakeMetaAds()
    resp = _client(monkeypatch, ads=ads).post(
        "/api/ads/meta/campaigns/c1/status", json={"status": "ACTIVE"}
    )
    assert resp.status_code == 409
    assert ads.status_changes == []


def test_set_campaign_status_requires_ads_management_scope(monkeypatch) -> None:
    # token solo ads_read → 422, sin llamar a Meta (premortem #4).
    ads = FakeMetaAds()
    store = _connected_store(scopes=("ads_read",))
    resp = _client(monkeypatch, store=store, ads=ads).post(
        "/api/ads/meta/campaigns/c1/status", json={"status": "PAUSED"}
    )
    assert resp.status_code == 422
    assert ads.status_changes == []


# ── analysis-input (seed REAL para el pod ads-analytics) ──────────────────────

def test_analysis_input_builds_pod_seed_from_real_insights(monkeypatch) -> None:
    raw = {
        "account_currency": "COP",
        "data": [
            {
                "date_start": "2026-06-15",
                "campaign_id": "c1",
                "campaign_name": "Duo",
                "spend": "120000",
                "inline_link_clicks": "80",
                "actions": [
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "40"}
                ],
            }
        ],
    }
    ads = FakeMetaAds(
        accounts=[MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)],
        raw_insights=raw,
    )
    body = _client(monkeypatch, store=_connected_store(), ads=ads).get(
        "/api/ads/meta/analysis-input?days=14"
    ).json()
    # el shape EXACTO que el pod ads-analytics consume
    assert body["meta_insights"]["account_currency"] == "COP"
    assert body["meta_insights"]["data"][0]["campaign_id"] == "c1"
    assert "manual_sales" in body
    assert "entities_payload" in body


def test_analysis_input_requires_connection(monkeypatch) -> None:
    resp = _client(monkeypatch).get("/api/ads/meta/analysis-input")
    assert resp.status_code == 409


def test_analysis_input_junta_las_ventas_reales_de_medusa(monkeypatch) -> None:
    """El join que faltaba (caso 424d6647 → verdict insufficient_data): el pod
    recibía `sales: []` y no podía blendear gasto contra revenue. `manual_sales`
    ahora sale de las órdenes PAGADAS de Medusa (OrderQueryPort del SDK),
    agregadas por día."""
    import datetime
    from types import SimpleNamespace

    def _ms(day: str) -> int:
        dt = datetime.datetime.fromisoformat(day + "T10:00").replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)

    today = datetime.date.today()
    d1 = (today - datetime.timedelta(days=2)).isoformat()
    d2 = (today - datetime.timedelta(days=1)).isoformat()
    orders = [
        SimpleNamespace(created_at_ms=_ms(d1), total_cop=600000, pay_status="paid",
                        status="delivered", is_draft=False),
        SimpleNamespace(created_at_ms=_ms(d1), total_cop=150000, pay_status="paid",
                        status="delivered", is_draft=False),
        SimpleNamespace(created_at_ms=_ms(d2), total_cop=999999, pay_status="pending",
                        status="new", is_draft=False),  # NO pagada → fuera
    ]

    class _FakeOrderQuery:
        async def list(self, *, limit=50, offset=0, include_drafts=True):
            batch = orders if offset == 0 else []
            return SimpleNamespace(orders=batch, count=len(orders), offset=offset,
                                   limit=limit, catalog_available=True, error_detail=None)

    monkeypatch.setattr(meta_oauth, "_orders", lambda: _FakeOrderQuery())
    ads = FakeMetaAds(
        accounts=[MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)],
        raw_insights={"account_currency": "COP", "data": []},
    )
    body = _client(monkeypatch, store=_connected_store(), ads=ads).get(
        "/api/ads/meta/analysis-input?days=14"
    ).json()

    assert body["manual_sales"] == {"sales": [
        {"date": d1, "total_orders": 2, "total_revenue": 750000},
    ]}


def test_analysis_input_degrada_a_sales_vacio_si_medusa_falla(monkeypatch) -> None:
    """Medusa caída NO puede tumbar el análisis: manual_sales degrada a []
    (el pod reporta insufficient_data honesto, no un 500 al operador)."""

    class _BrokenOrderQuery:
        async def list(self, **kw):
            raise RuntimeError("Medusa no responde")

    monkeypatch.setattr(meta_oauth, "_orders", lambda: _BrokenOrderQuery())
    ads = FakeMetaAds(
        accounts=[MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)],
        raw_insights={"account_currency": "COP", "data": []},
    )
    body = _client(monkeypatch, store=_connected_store(), ads=ads).get(
        "/api/ads/meta/analysis-input?days=14"
    ).json()
    assert body["manual_sales"] == {"sales": []}
