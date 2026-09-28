"""La API de Ads no retiene memoria sin límite entre requests (incidente 2026-09-25).

La caja de prod (4 GB, sin swap) se congeló ~25 min. Medido en prod con
tracemalloc: cada request a los endpoints de Ads dejaba ~8.1 MB retenidos para
siempre. El cache del scan del vault usaba de key el `since_ms` exacto
(`now - días`, cambia cada milisegundo): nunca acertaba y cada request
insertaba una entrada nueva con todas las sesiones de la ventana; el TTL solo
se miraba al leer, así que nada se borraba.

Estos tests fijan el contrato de la capa API: requests seguidos comparten un
scan, y el cache queda acotado por más ventanas distintas que se pidan.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.ads.api as ads_mod
from src.plugins.ads.meta.client import FakeMetaAds, MetaAdCreative
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken

# Múltiplo exacto de 60 s: +100 y +107 ms caen en el mismo minuto.
_MINUTE_ALIGNED_MS = 1_700_000_040_000


@pytest.fixture
def scan_calls(monkeypatch, tmp_path: Path):
    """`scan_ad_sessions` falso que registra el `since_ms` de cada scan."""
    calls: list[int | None] = []

    def fake_scan(vault_dir, *, since_ms=None):
        calls.append(since_ms)
        return [(tmp_path / f"wa_{len(calls)}", {"origin": {"channel": "ad"}})]

    monkeypatch.setattr(ads_mod, "scan_ad_sessions", fake_scan)
    ads_mod._scan_cache.clear()
    yield calls
    ads_mod._scan_cache.clear()


def test_sessions_requested_milliseconds_apart_share_one_scan(scan_calls):
    first = ads_mod._cached_sessions(_MINUTE_ALIGNED_MS + 100)
    second = ads_mod._cached_sessions(_MINUTE_ALIGNED_MS + 107)

    assert len(scan_calls) == 1
    assert second is first
    assert len(ads_mod._scan_cache) == 1
    # La ventana escaneada cubre las dos pedidas (el scan prefiltra por mtime).
    assert scan_calls[0] <= _MINUTE_ALIGNED_MS + 100


def test_campaigns_requested_twice_in_a_row_scan_the_vault_once(monkeypatch, tmp_path: Path):
    """El caso de prod: el dashboard refetchea `/campaigns?days=30` en cada
    evento SSE de `orders` — requests a milisegundos de distancia."""
    real_scan = ads_mod.scan_ad_sessions
    scans: list[int | None] = []

    def counting_scan(vault_dir, *, since_ms=None):
        scans.append(since_ms)
        return real_scan(vault_dir, since_ms=since_ms)

    now_s = iter(_MINUTE_ALIGNED_MS / 1000 + 0.1 + i * 0.003 for i in range(1000))
    monkeypatch.setattr(ads_mod, "scan_ad_sessions", counting_scan)
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "")
    monkeypatch.setattr(ads_mod, "_cached_meta_campaigns", lambda since_ms, until_ms: ([], []))
    monkeypatch.setattr(ads_mod.time, "time", lambda: next(now_s))
    ads_mod._scan_cache.clear()
    app = FastAPI()
    app.include_router(ads_mod.router, prefix="/api/ads")
    with patch.object(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        client = TestClient(app)
        assert client.get("/api/ads/campaigns", params={"days": 30}).status_code == 200
        assert client.get("/api/ads/campaigns", params={"days": 30}).status_code == 200

    assert len(scans) == 1
    assert len(ads_mod._scan_cache) == 1
    ads_mod._scan_cache.clear()

def test_scan_cache_stays_bounded_across_many_windows(scan_calls):
    # 20 ventanas distintas (una por día) — p. ej. rangos custom del operador.
    for day in range(20):
        ads_mod._cached_sessions(_MINUTE_ALIGNED_MS - day * ads_mod._DAY_MS)

    assert len(scan_calls) == 20
    assert len(ads_mod._scan_cache) <= 4


# --- caches de Meta: una entrada por ventana / por set de ads ---------------


@pytest.fixture
def meta_connected(monkeypatch):
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    monkeypatch.setattr(ads_mod, "_meta_store", lambda: store)
    monkeypatch.setattr(ads_mod, "_meta_ads", lambda: FakeMetaAds())
    caches = (ads_mod._meta_campaign_cache, ads_mod._meta_adset_cache, ads_mod._meta_ad_cache)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


@pytest.mark.parametrize(
    ("fetch", "cache"),
    [
        ("_cached_meta_campaigns", "_meta_campaign_cache"),
        ("_cached_meta_adsets", "_meta_adset_cache"),
        ("_cached_meta_ads", "_meta_ad_cache"),
    ],
)
def test_meta_metrics_cache_stays_bounded_across_many_windows(meta_connected, fetch, cache):
    # 40 ventanas distintas: un rango custom por día, o el mismo preset días seguidos.
    for day in range(40):
        getattr(ads_mod, fetch)(_MINUTE_ALIGNED_MS - day * ads_mod._DAY_MS, None)

    assert len(getattr(ads_mod, cache)) <= 16


def test_meta_names_cache_stays_bounded_across_many_ad_sets(monkeypatch):
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "TOK")
    monkeypatch.setattr(
        ads_mod, "fetch_meta_ad_names", lambda ad_ids, *, token, transport=None: {}
    )
    ads_mod._meta_names_cache.clear()

    # Cada chat nuevo que entra por un anuncio cambia el set de ads del scan.
    for n in range(40):
        ads_mod._cached_meta_names([f"AD_{i}" for i in range(n + 1)])

    assert len(ads_mod._meta_names_cache) <= 16
    ads_mod._meta_names_cache.clear()


def test_creative_cache_stays_bounded_across_many_ads(monkeypatch):
    creatives = {
        f"AD_{i}": MetaAdCreative(f"AD_{i}", None, None, None, None, None, None)
        for i in range(100)
    }
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    monkeypatch.setattr(ads_mod, "_meta_store", lambda: store)
    monkeypatch.setattr(ads_mod, "_meta_ads", lambda: FakeMetaAds(creatives=creatives))
    ads_mod._meta_creative_cache.clear()

    # El operador abre el inspector de muchos anuncios distintos.
    for ad_id in creatives:
        ads_mod.get_ad_creative(ad_id)

    assert len(ads_mod._meta_creative_cache) <= 64
    ads_mod._meta_creative_cache.clear()
