"""Merge de campañas Meta en el listado del dashboard (lista izquierda unificada).

Pedido del operador (2026-07-09): las campañas de Meta deben aparecer en la
lista de campañas IGUAL que las derivadas del vault ("clientes directos") —
seleccionables a la izquierda, con el canvas central pintando su info.

Reglas del merge (puro, `meta_merge.merge_meta_campaigns`):
- bucket del vault con `meta_campaign_id` que matchea UNA campaña Meta → se le
  llenan spend/impressions/reach/clicks/status/objective.
- campaña Meta sin bucket → entrada standalone (started=0, conversations=None,
  source_type="ad", id=campaign_id de Meta).
- campaña Meta con VARIOS buckets → standalone con las métricas + buckets
  intactos (llenar spend campaign-level en N buckets duplicaría el gasto).

Y el wiring HTTP: `GET /api/ads/campaigns` incluye las campañas Meta cuando el
token store tiene conexión; sin conexión el comportamiento actual no cambia.
"""
from __future__ import annotations


from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.ads.api as ads_mod
from src.plugins.ads.aggregation import AdsCampaignSummary
from src.plugins.ads.meta.client import FakeMetaAds, MetaCampaignMeta
from src.plugins.ads.meta.parse import MetaCampaignMetrics
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken
from src.plugins.ads.meta_merge import merge_meta_campaigns


def _bucket(id: str, meta_campaign_id: str | None = None) -> AdsCampaignSummary:
    return AdsCampaignSummary(
        id=id,
        name=f"bucket {id}",
        source_type="ad",
        started=3,
        first_seen_ms=1,
        last_seen_ms=2,
        conversations={"nuevo": 1, "activo": 0, "calificado": 0, "cotizado": 0,
                       "ganado": 1, "perdido": 1, "no_reply": 0},
        meta_campaign_id=meta_campaign_id,
    )


_META = [MetaCampaignMeta("c-1", "Día del padre", "ACTIVE", "OUTCOME_SALES")]
_METRICS = [MetaCampaignMetrics("c-1", "Día del padre", 896823.0, 45000, 38000, 571, 205)]


# ── merge puro ────────────────────────────────────────────────────────────────

def test_meta_only_campaign_becomes_standalone_entry() -> None:
    merged = merge_meta_campaigns([_bucket("AD_9")], _META, _METRICS)
    ids = [c.id for c in merged]
    assert "AD_9" in ids and "c-1" in ids
    standalone = next(c for c in merged if c.id == "c-1")
    assert standalone.name == "Día del padre"
    assert standalone.source_type == "ad"
    assert standalone.started == 0
    assert standalone.conversations is None
    assert standalone.spend == 896823.0
    assert standalone.clicks == 571
    assert standalone.status == "active"  # normalizado a minúscula (contract UI)
    assert standalone.objective == "OUTCOME_SALES"
    assert standalone.meta_campaign_id == "c-1"


def test_single_matched_bucket_gets_meta_fields_filled() -> None:
    merged = merge_meta_campaigns([_bucket("AD_1", meta_campaign_id="c-1")], _META, _METRICS)
    assert [c.id for c in merged] == ["AD_1"]  # sin entrada duplicada
    filled = merged[0]
    assert filled.spend == 896823.0
    assert filled.impressions == 45000
    assert filled.reach == 38000
    assert filled.status == "active"
    assert filled.objective == "OUTCOME_SALES"
    # lo del vault queda intacto
    assert filled.started == 3
    assert filled.conversations is not None


def test_multiple_matched_buckets_stay_intact_and_meta_goes_standalone() -> None:
    buckets = [_bucket("AD_1", "c-1"), _bucket("AD_2", "c-1")]
    merged = merge_meta_campaigns(buckets, _META, _METRICS)
    assert [c.id for c in merged][:2] == ["AD_1", "AD_2"]
    assert merged[0].spend is None and merged[1].spend is None  # sin doble conteo
    standalone = next(c for c in merged if c.id == "c-1")
    assert standalone.spend == 896823.0


def test_metrics_without_campaign_meta_still_merge() -> None:
    # Insights puede traer una campaña que list_campaigns no listó (borrada/archivada):
    # entra igual con status/objective en None.
    merged = merge_meta_campaigns([], [], _METRICS)
    assert merged[0].id == "c-1" and merged[0].spend == 896823.0
    assert merged[0].status is None


# ── wiring HTTP ───────────────────────────────────────────────────────────────

def _client(monkeypatch, tmp_path, *, store, ads) -> TestClient:
    monkeypatch.setattr(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(ads_mod, "_meta_store", lambda: store)
    monkeypatch.setattr(ads_mod, "_meta_ads", lambda: ads)
    ads_mod._scan_cache.clear()
    ads_mod._meta_campaign_cache.clear()
    app = FastAPI()
    app.include_router(ads_mod.router, prefix="/api/ads")
    return TestClient(app)


def _connected() -> InMemoryTokenStore:
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    return store


def test_campaigns_endpoint_includes_meta_when_connected(monkeypatch, tmp_path) -> None:
    ads = FakeMetaAds(campaigns=_META, metrics=_METRICS)
    client = _client(monkeypatch, tmp_path, store=_connected(), ads=ads)
    body = client.get("/api/ads/campaigns?days=30").json()
    ids = [c["id"] for c in body["campaigns"]]
    assert "c-1" in ids
    meta_row = next(c for c in body["campaigns"] if c["id"] == "c-1")
    assert meta_row["spend"] == 896823.0
    assert meta_row["status"] == "active"


def test_campaigns_endpoint_unchanged_without_connection(monkeypatch, tmp_path) -> None:
    ads = FakeMetaAds(campaigns=_META, metrics=_METRICS)
    client = _client(monkeypatch, tmp_path, store=InMemoryTokenStore(), ads=ads)
    body = client.get("/api/ads/campaigns?days=30").json()
    assert body == {"campaigns": []}  # vault vacío y sin conexión → sin invento
