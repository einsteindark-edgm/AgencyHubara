"""Tests del wiring HTTP del endpoint `ads` — el filtro por rango de fechas.

Los tests del use case (`tests/test_list_ads_campaigns.py`) ya cubren la lógica
de agregación + filtro (`since_ms`/`until_ms`). Acá validamos lo que SOLO existe
en la capa API y que el use case no ve:

  1. `_window`: traduce `?from=&to=` (YYYY-MM-DD) a `(since_ms, until_ms)`, con
     `until` exclusivo, orden tolerante y degradación leniente en fecha inválida.
  2. El alias `from` (keyword reservada en Python → param `frm`) se parsea, y la
     ventana efectivamente recorta la agregación end-to-end vía FastAPI.

Montamos el router en una app local (sin depender del prefix del manifest) y
parcheamos `WORKSPACE_VAULT_DIR` del módulo ads + limpiamos su cache de scans.
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
    bogota_day_start_ms,
)

_DAY_MS = 24 * 60 * 60 * 1000


# --- _window (traducción rango → since/until) ------------------------------


def test_window_preset_only_lower_bound():
    """Preset (`days`): `since` acotado, `until` abierto (hasta ahora)."""
    since, until = ads_mod._window(30, None, None)
    assert since is not None and until is None


def test_window_custom_from_to_inclusive():
    """`from`/`to` → [midnight(from), midnight(to)+1día) — `to` inclusive."""
    since, until = ads_mod._window(None, "2026-05-01", "2026-05-07")
    assert since == bogota_day_start_ms("2026-05-01")
    assert until == bogota_day_start_ms("2026-05-07") + _DAY_MS


def test_window_custom_wins_over_days():
    """Con `from`+`to` presentes, `days` se ignora."""
    assert ads_mod._window(30, "2026-05-01", "2026-05-07") == ads_mod._window(
        None, "2026-05-01", "2026-05-07"
    )


def test_window_tolerates_reversed_range():
    """`from` > `to` se intercambia (no devuelve ventana vacía)."""
    since, until = ads_mod._window(None, "2026-05-07", "2026-05-01")
    assert since == bogota_day_start_ms("2026-05-01")
    assert until == bogota_day_start_ms("2026-05-07") + _DAY_MS


def test_window_invalid_date_degrades_to_open():
    """Fecha inválida → ventana abierta (None, None), no 422/crash."""
    assert ads_mod._window(None, "not-a-date", "2026-05-07") == (None, None)


# --- endpoint end-to-end (alias `from` + ventana aplicada) -----------------


@pytest.fixture
def ads_client(tmp_path: Path):
    ads_mod._scan_cache.clear()
    with patch.object(ads_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        app = FastAPI()
        app.include_router(ads_mod.router, prefix="/api/ads")
        yield TestClient(app), tmp_path
    ads_mod._scan_cache.clear()


def _seed_two_episodes(vault: Path) -> None:
    """Una campaña AD_X con un episodio en mayo (en rango) y otro en marzo."""
    in_ms = bogota_day_start_ms("2026-05-15") + 12 * 60 * 60 * 1000
    out_ms = bogota_day_start_ms("2026-03-10") + 12 * 60 * 60 * 1000
    sd = vault / "wa_111"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "metadata.json").write_text(
        json.dumps(
            {
                "origin": {
                    "channel": "ad",
                    "first_seen_ms": out_ms,
                    "headline": "Velas",
                    "source_id": "AD_X",
                },
                "active_route": "ventas",
                "episodes": [
                    {"episode_id": "ep_mar", "started_at_ms": out_ms, "closed_at_ms": out_ms + 1000},
                    {"episode_id": "ep_may", "started_at_ms": in_ms, "closed_at_ms": in_ms + 1000},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_campaigns_endpoint_applies_custom_range_via_from_alias(ads_client):
    """`?from=&to=` (alias de `from`) recorta la agregación: solo el episodio de
    mayo cuenta cuando la ventana es mayo."""
    client, vault = ads_client
    _seed_two_episodes(vault)

    full = client.get("/api/ads/campaigns")
    assert full.status_code == 200
    camp_full = next(c for c in full.json()["campaigns"] if c["id"] == "AD_X")
    assert camp_full["started"] == 2  # sin filtro: ambos episodios

    windowed = client.get(
        "/api/ads/campaigns", params={"from": "2026-05-01", "to": "2026-05-31"}
    )
    assert windowed.status_code == 200
    camp_win = next(c for c in windowed.json()["campaigns"] if c["id"] == "AD_X")
    assert camp_win["started"] == 1  # solo ep_may en la ventana de mayo


def test_daily_endpoint_custom_range_length_matches_series(ads_client):
    """El endpoint diario con `from`/`to` devuelve una serie del largo del rango
    y `days` = longitud real de la serie."""
    client, vault = ads_client
    _seed_two_episodes(vault)

    resp = client.get(
        "/api/ads/campaigns/AD_X/daily",
        params={"from": "2026-05-01", "to": "2026-05-07"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7  # 1..7 may inclusive
    assert len(body["series"]) == 7
    assert body["series"][0]["d"] == "1 may"
    assert body["series"][-1]["d"] == "7 may"


# --- segmentación: agrupación por campaña + drill-down por adset ------------


def _seed_ad_session(vault: Path, phone: str, source_id: str, started_ms: int) -> None:
    sd = vault / f"wa_{phone}"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "metadata.json").write_text(
        json.dumps(
            {
                "origin": {
                    "channel": "ad",
                    "first_seen_ms": started_ms,
                    "headline": "Chatea",
                    "source_id": source_id,
                },
                "active_route": "ventas",
                "episodes": [
                    {
                        "episode_id": "ep_001",
                        "started_at_ms": started_ms,
                        "closed_at_ms": started_ms + 1000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


_SEG_NAMES = {
    "AD_1": {
        "ad_name": "Ad uno", "campaign_name": "Día del Padre",
        "campaign_id": "CAMP_9", "adset_id": "ADSET_A",
        "adset_name": "Hombres 25-45", "thumbnail_url": None,
    },
    "AD_2": {
        "ad_name": "Ad dos", "campaign_name": "Día del Padre",
        "campaign_id": "CAMP_9", "adset_id": "ADSET_B",
        "adset_name": "Mujeres 30-50", "thumbnail_url": None,
    },
}


@pytest.fixture
def segmented_client(ads_client, monkeypatch):
    """Dos ads de la MISMA campaña (segmentos distintos) + resolver fake."""
    client, vault = ads_client
    base = bogota_day_start_ms("2026-05-15") + 12 * 60 * 60 * 1000
    _seed_ad_session(vault, "111", "AD_1", base)
    _seed_ad_session(vault, "222", "AD_2", base + 1000)
    monkeypatch.setattr(
        ads_mod, "fetch_meta_ad_names", lambda ad_ids, *, token, transport=None: _SEG_NAMES
    )
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "TOK")
    ads_mod._meta_names_cache.clear()
    yield client, vault
    ads_mod._meta_names_cache.clear()


def test_campaigns_endpoint_groups_ads_of_same_campaign(segmented_client):
    """Segmentación (2026-07-10): dos ads de la misma campaña Meta → UNA
    fila con id = campaign_id y agregados sumados (antes: una fila por ad)."""
    client, _ = segmented_client
    resp = client.get("/api/ads/campaigns")
    assert resp.status_code == 200
    rows = resp.json()["campaigns"]
    ids = [c["id"] for c in rows]
    assert "CAMP_9" in ids
    assert "AD_1" not in ids and "AD_2" not in ids
    camp = next(c for c in rows if c["id"] == "CAMP_9")
    assert camp["started"] == 2
    assert camp["name"] == "Día del Padre"
    assert camp["meta_campaign_id"] == "CAMP_9"


def test_adsets_endpoint_returns_segment_rows(segmented_client, monkeypatch):
    """GET /campaigns/{id}/adsets → una fila por segmento con agregados del
    vault + métricas Meta level=adset cuando hay conexión."""
    from src.plugins.ads.meta.parse import MetaAdsetMetrics

    client, _ = segmented_client
    monkeypatch.setattr(
        ads_mod,
        "_cached_meta_adsets",
        lambda since_ms, until_ms: [
            MetaAdsetMetrics(
                adset_id="ADSET_A", adset_name="Hombres 25-45",
                campaign_id="CAMP_9", spend=320500.0, impressions=15000,
                reach=12100, clicks=210, messaging_conversations_started=44,
            )
        ],
        raising=False,
    )
    resp = client.get("/api/ads/campaigns/CAMP_9/adsets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == "CAMP_9"
    by_id = {r["id"]: r for r in body["ad_sets"]}
    assert set(by_id) == {"ADSET_A", "ADSET_B"}
    assert by_id["ADSET_A"]["name"] == "Hombres 25-45"
    assert by_id["ADSET_A"]["started"] == 1
    assert by_id["ADSET_A"]["spend"] == 320500.0
    assert by_id["ADSET_B"]["spend"] is None  # sin métrica → None honesto


def test_conversations_endpoint_accepts_campaign_and_adset_scope(
    segmented_client,
):
    """El drill-down funciona con el id AGRUPADO: /CAMP_9/conversations trae
    los chats de todos sus ads; ?adset_id= acota al segmento."""
    client, _ = segmented_client

    all_convs = client.get("/api/ads/campaigns/CAMP_9/conversations").json()
    assert {c["phone_number"] for c in all_convs["conversations"]} == {
        "111", "222",
    }

    seg = client.get(
        "/api/ads/campaigns/CAMP_9/conversations",
        params={"adset_id": "ADSET_B"},
    ).json()
    assert {c["phone_number"] for c in seg["conversations"]} == {"222"}


def test_daily_endpoint_accepts_adset_scope(segmented_client):
    client, _ = segmented_client
    resp = client.get(
        "/api/ads/campaigns/CAMP_9/daily",
        params={"from": "2026-05-14", "to": "2026-05-16", "adset_id": "ADSET_A"},
    )
    assert resp.status_code == 200
    total = sum(
        sum(v for k, v in p.items() if k != "d") for p in resp.json()["series"]
    )
    assert total == 1  # solo el episodio de AD_1 (segmento A)


def test_legacy_raw_source_id_still_works(ads_client):
    """Back-compat: sin resolver (Graph caído / sin token) el id crudo
    (source_id) sigue sirviendo el drill-down como antes."""
    client, vault = ads_client
    _seed_two_episodes(vault)
    resp = client.get("/api/ads/campaigns/AD_X/conversations")
    assert resp.status_code == 200
    assert len(resp.json()["conversations"]) == 2


# --- enrichment de nombres Meta (fix 2026-07-01) ----------------------------


def test_campaigns_endpoint_enriches_names_from_meta(ads_client, monkeypatch):
    """El endpoint resuelve el nombre REAL del ad/campaña vía Marketing API
    (batch + cache TTL) y lo usa como `name`; el headline del referral
    queda en `creative_title`. Sin token / API caída → headlines intactos
    (best-effort, cubierto por el caso else)."""
    client, vault = ads_client
    _seed_two_episodes(vault)

    calls: list[list[str]] = []

    def fake_fetch(ad_ids, *, token, transport=None):
        calls.append(sorted(ad_ids))
        return {
            "AD_X": {
                "ad_name": "Ad velas premium",
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_1",
            }
        }

    monkeypatch.setattr(ads_mod, "fetch_meta_ad_names", fake_fetch)
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "TOK")
    ads_mod._meta_names_cache.clear()

    resp = client.get("/api/ads/campaigns")
    assert resp.status_code == 200
    # Segmentación (2026-07-10): la fila resuelta se agrupa por campaña —
    # su id pasa a ser el campaign_id de Meta (estable para el drill-down).
    camp = next(c for c in resp.json()["campaigns"] if c["id"] == "CAMP_1")
    assert camp["name"] == "Día del Padre 2026 · Ad velas premium"
    assert camp["creative_title"] == "Velas"
    assert camp["meta_campaign_id"] == "CAMP_1"

    # 2do request: cache TTL — no re-fetchea
    client.get("/api/ads/campaigns")
    assert len(calls) == 1

    ads_mod._meta_names_cache.clear()


def test_campaigns_endpoint_without_token_keeps_headlines(ads_client, monkeypatch):
    """Sin token configurado, no se llama a Meta y el name queda el headline."""
    client, vault = ads_client
    _seed_two_episodes(vault)
    monkeypatch.setattr(ads_mod, "_meta_names_token", lambda: "")
    ads_mod._meta_names_cache.clear()

    resp = client.get("/api/ads/campaigns")
    camp = next(c for c in resp.json()["campaigns"] if c["id"] == "AD_X")
    assert camp["name"] == "Velas"
    assert camp["creative_title"] is None
