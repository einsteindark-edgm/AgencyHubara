"""Las ventanas del panel de Ads miden los mismos días en chats y en gasto de Meta.

Auditoría 2026-11-10:

  * Preset «7d/30d/90d»: los chats contaban `ahora − N×24 h` (ventana corrida)
    pero Meta recibe días calendario (N+1 días, de hoy − N a hoy) y el gráfico
    diario pinta N días calendario. El gasto traía ~18 h de más sin sus chats, y
    las barras del gráfico no sumaban el KPI «Chats iniciados». Ahora un preset
    son N días calendario de Bogotá terminando hoy, para chats, gráfico y Meta.
  * «Total»: los chats eran todo el historial y el gasto solo los últimos 90
    días → ROAS inflado en campañas viejas. Ahora el gasto arranca el día del
    primer chat del vault.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.ads.api as ads_mod
from src.plugins.ads.aggregation import bogota_day_start_ms
from src.plugins.ads.meta.client import FakeMetaAds
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken

_HOUR_MS = 60 * 60 * 1000
# Lunes 2026-11-09, 17:47 en Bogotá.
_NOW_MS = bogota_day_start_ms("2026-11-09") + 17 * _HOUR_MS + 47 * 60 * 1000


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(ads_mod, "_now_ms", lambda: _NOW_MS)


class RecordingMetaAds(FakeMetaAds):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[str, str]] = []

    def fetch_campaign_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_campaign_metrics(token, account_id, since=since, until=until)

    def fetch_adset_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_adset_metrics(token, account_id, since=since, until=until)

    def fetch_ad_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_ad_metrics(token, account_id, since=since, until=until)


@pytest.fixture
def client(tmp_path: Path, monkeypatch, clock):
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    meta = RecordingMetaAds()
    monkeypatch.setattr(ads_mod, "_meta_store", lambda: store)
    monkeypatch.setattr(ads_mod, "_meta_ads", lambda: meta)
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "")
    caches = (
        ads_mod._scan_cache,
        ads_mod._meta_campaign_cache,
        ads_mod._meta_adset_cache,
        ads_mod._meta_ad_cache,
    )
    for cache in caches:
        cache.clear()
    with patch.object(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        app = FastAPI()
        app.include_router(ads_mod.router, prefix="/api/ads")
        yield TestClient(app), tmp_path, meta
    for cache in caches:
        cache.clear()


def _seed_chat(vault: Path, phone: str, started_ms: int) -> None:
    d = vault / f"wa_{phone}"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "origin": {"channel": "ad", "source_id": "AD_X", "first_seen_ms": started_ms},
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "started_at_ms": started_ms,
                        "closed_at_ms": started_ms + 60_000,
                        "closing_tag": "RECHAZO",
                        "referral_snapshot": {
                            "channel": "ad", "source_id": "AD_X", "headline": "Velas",
                        },
                    }
                ],
            }
        )
    )


def test_preset_window_is_n_calendar_days_ending_today(clock) -> None:
    since_ms, until_ms = ads_mod._window(7, None, None)

    assert since_ms == bogota_day_start_ms("2026-11-03")
    assert until_ms is None


def test_preset_kpi_and_daily_chart_count_the_same_chats(client) -> None:
    http, vault, _meta = client
    # 6 días y 19 h antes: dentro de 7×24 h, pero del día 2 de noviembre.
    _seed_chat(vault, "573001", bogota_day_start_ms("2026-11-02") + 23 * _HOUR_MS)
    _seed_chat(vault, "573002", bogota_day_start_ms("2026-11-03") + _HOUR_MS // 2)

    [campaign] = http.get("/api/ads/campaigns", params={"days": 7}).json()["campaigns"]
    series = http.get("/api/ads/campaigns/AD_X/daily", params={"days": 7}).json()["series"]

    bars = sum(sum(v for k, v in point.items() if k != "d") for point in series)
    assert (series[0]["d"], series[-1]["d"]) == ("3 nov", "9 nov")
    assert campaign["started"] == bars == 1


@pytest.mark.parametrize(
    "fetch", ["_cached_meta_campaigns", "_cached_meta_adsets", "_cached_meta_ads"]
)
def test_preset_asks_meta_the_same_calendar_days(client, fetch) -> None:
    _http, _vault, meta = client
    since_ms, until_ms = ads_mod._window(7, None, None)

    getattr(ads_mod, fetch)(since_ms, until_ms)

    assert meta.windows == [("2026-11-03", "2026-11-09")]


@pytest.mark.parametrize(
    "url",
    [
        "/api/ads/campaigns",
        "/api/ads/campaigns/CAMP_9/adsets",
        "/api/ads/campaigns/CAMP_9/adsets/ADSET_A/ads",
    ],
)
def test_total_asks_meta_for_spend_since_the_first_chat(client, url) -> None:
    http, vault, meta = client
    _seed_chat(vault, "573001", bogota_day_start_ms("2026-05-15") + 10 * _HOUR_MS)
    _seed_chat(vault, "573002", bogota_day_start_ms("2026-11-05") + 10 * _HOUR_MS)

    assert http.get(url).status_code == 200

    assert meta.windows == [("2026-05-15", "2026-11-09")]
