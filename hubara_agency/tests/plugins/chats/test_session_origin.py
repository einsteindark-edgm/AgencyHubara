"""Origen REAL de la conversación en el dashboard de Chats.

Antes el panel "Estado actual" del inspector mostraba un origen hardcodeado
("Meta Ads · velas") y la API no exponía nada. El ingest SÍ persiste el
referral CTWA (`metadata.origin` sticky first-touch + `episodes[].referral_snapshot`
por episodio, con `source_id` = ad id) y el plugin ads ya resuelve el nombre
real de la campaña vía Graph. Acá: `session_origin` (pura) + `origin` en
`/api/dashboard/sessions` y `/api/dashboard/sessions/{id}` con el nombre de
campaña resuelto best-effort.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard
from src.plugins.chats.shared.origin import session_origin

_AD_SNAPSHOT = {
    "channel": "ad",
    "source_id": "AD_001",
    "source_type": "ad",
    "headline": "Velas aromáticas",
    "ctwa_clid": "CLID_X",
}


class TestSessionOrigin:
    def test_none_without_origin_or_episodes(self) -> None:
        assert session_origin(None) is None
        assert session_origin({}) is None

    def test_prefers_last_episode_referral_snapshot(self) -> None:
        """Last-ad-touch (fix 2026-07-01): el episodio re-atribuye; el origin
        sticky solo aporta el first-touch."""
        metadata = {
            "origin": {"channel": "direct", "first_seen_ms": 1000, "headline": None, "source_id": None},
            "episodes": [
                {"episode_id": "ep_001", "started_at_ms": 1000, "referral_snapshot": None},
                {"episode_id": "ep_002", "started_at_ms": 5000, "referral_snapshot": _AD_SNAPSHOT},
            ],
        }
        assert session_origin(metadata) == {
            "channel": "ad",
            "source_id": "AD_001",
            "source_type": "ad",
            "headline": "Velas aromáticas",
            "first_seen_ms": 1000,
            "campaign_name": None,
            "ad_name": None,
        }

    def test_falls_back_to_sticky_origin(self) -> None:
        metadata = {
            "origin": {"channel": "ad", "first_seen_ms": 1000, "headline": "Velas", "source_id": "AD_9"},
            "episodes": [{"episode_id": "ep_001", "started_at_ms": 1000, "referral_snapshot": None}],
        }
        out = session_origin(metadata)
        assert out is not None
        assert (out["channel"], out["source_id"], out["headline"], out["first_seen_ms"]) == ("ad", "AD_9", "Velas", 1000)

    def test_direct_origin_is_reported_as_direct(self) -> None:
        out = session_origin({"origin": {"channel": "direct", "first_seen_ms": 7, "headline": None, "source_id": None}})
        assert out is not None and out["channel"] == "direct" and out["source_id"] is None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    monkeypatch.setattr(
        dashboard,
        "_resolve_ad_names",
        lambda ids: {"AD_001": {"campaign_name": "Día del Padre", "ad_name": "Velas CTA"}} if "AD_001" in ids else {},
    )
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed(vault: Path, sid: str, data: dict) -> None:
    (vault / sid).mkdir(parents=True, exist_ok=True)
    (vault / sid / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def test_sessions_list_and_detail_expose_origin_with_campaign_name(client) -> None:
    c, vault = client
    _seed(vault, "wa_573114842180", {
        "tag": "INTERESADO",
        "origin": {"channel": "ad", "first_seen_ms": 1789006000000, "headline": "Velas aromáticas", "source_id": "AD_001"},
        "episodes": [{"episode_id": "ep_001", "started_at_ms": 1789006000000, "referral_snapshot": _AD_SNAPSHOT}],
    })
    _seed(vault, "wa_573000000001", {"tag": "NO_ETIQUETADO"})

    body = c.get("/api/dashboard/sessions").json()
    by_id = {s["session_id"]: s for s in body["sessions"]}
    assert by_id["wa_573114842180"]["origin"] == {
        "channel": "ad", "source_id": "AD_001", "source_type": "ad", "headline": "Velas aromáticas",
        "first_seen_ms": 1789006000000, "campaign_name": "Día del Padre", "ad_name": "Velas CTA",
    }
    assert by_id["wa_573000000001"]["origin"] is None

    detail = c.get("/api/dashboard/sessions/wa_573114842180").json()
    assert detail["origin"]["campaign_name"] == "Día del Padre"
    assert c.get("/api/dashboard/sessions/wa_573000000001").json()["origin"] is None


def test_origin_degrades_to_headline_when_names_unavailable(client, monkeypatch) -> None:
    c, vault = client
    monkeypatch.setattr(dashboard, "_resolve_ad_names", lambda ids: {})
    _seed(vault, "wa_573114842180", {
        "origin": {"channel": "ad", "first_seen_ms": 1, "headline": "Velas aromáticas", "source_id": "AD_001"},
    })
    origin = c.get("/api/dashboard/sessions/wa_573114842180").json()["origin"]
    assert origin["campaign_name"] is None and origin["headline"] == "Velas aromáticas"
