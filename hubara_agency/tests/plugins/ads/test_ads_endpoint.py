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
    camp = next(c for c in resp.json()["campaigns"] if c["id"] == "AD_X")
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
