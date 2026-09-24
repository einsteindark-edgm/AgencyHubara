"""Control del encendido del bot nuevo (plan del laboratorio PR 16): contrato
`perception-rollout@v1` de chats, que el panel de Agents consume por cast.

GET devuelve el estado, el techo de Terraform, los chequeos de cada modo y
las métricas de la sombra. PUT mueve el modo DENTRO del techo: apagar y bajar
siempre pasan; subir sin cumplir los chequeos da 422 con los que fallan.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import perception as api


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "_now_ms", lambda: 1_790_200_000_000)
    monkeypatch.setenv("SALES_PERCEPTION_MODE_CEILING", "on")
    monkeypatch.setenv("SALES_PERCEPTION_PROFILE", "jev-v1")
    monkeypatch.setenv("SALES_SIGNAL_INBOUND_META", "on")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    return TestClient(app)


def test_get_reports_state_ceiling_checks_and_metrics(client: TestClient) -> None:
    body = client.get("/api/chats/perception/rollout").json()

    assert body["state"]["mode"] == "off"
    assert (body["ceiling"], body["profile"]) == ("on", "jev-v1")
    assert body["can"]["shadow"] == []
    assert "shadow_days" in body["can"]["canary"]
    assert body["metrics"] == {"days": 0, "turns": 0, "fallback_rate": None, "p95_ms": None}
    assert {c["code"] for c in body["readiness"]["canary"]} >= {"signal_meta_on", "shadow_days", "shadow_p95"}


def test_put_moves_the_mode_within_the_ceiling(client: TestClient, tmp_path: Path) -> None:
    res = client.put("/api/chats/perception/rollout", json={"mode": "shadow"})

    assert res.status_code == 200 and res.json()["state"]["mode"] == "shadow"
    saved = json.loads((tmp_path / "_rollout" / "perception.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "shadow" and saved["updated_at_ms"] == 1_790_200_000_000


def test_raising_without_the_shadow_bar_is_422_with_the_failing_checks(client: TestClient) -> None:
    client.put("/api/chats/perception/rollout", json={"mode": "shadow"})

    res = client.put("/api/chats/perception/rollout", json={"mode": "on"})

    assert res.status_code == 422
    assert "shadow_days" in res.json()["detail"]["failing"]


def test_turning_off_always_works(client: TestClient, monkeypatch) -> None:
    client.put("/api/chats/perception/rollout", json={"mode": "shadow"})
    monkeypatch.setenv("SALES_SIGNAL_INBOUND_META", "off")
    monkeypatch.delenv("OPENROUTER_API_KEY")

    res = client.put("/api/chats/perception/rollout", json={"mode": "off"})

    assert res.status_code == 200 and res.json()["state"]["mode"] == "off"


def test_canary_settings_are_validated(client: TestClient) -> None:
    bad_percent = client.put("/api/chats/perception/rollout", json={"mode": "off", "canary_percent": 150})
    bad_number = client.put("/api/chats/perception/rollout", json={"mode": "off", "test_numbers": ["573001234567"]})

    assert bad_percent.status_code == 422 and bad_number.status_code == 422
    ok = client.put("/api/chats/perception/rollout", json={"mode": "off", "canary_percent": 10, "test_numbers": ["wa_573001234567"]})
    assert ok.status_code == 200 and ok.json()["state"]["test_numbers"] == ["wa_573001234567"]
