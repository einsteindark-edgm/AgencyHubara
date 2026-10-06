"""Control del encendido del bot nuevo (plan del laboratorio PR 16): contrato
`perception-rollout@v1` de chats, que el panel de Agents consume por cast.

GET devuelve el estado, el techo de Terraform, los chequeos de cada modo,
las métricas de la sombra y el resumen de la sonda diaria de Jev.

Los cambios van SOLO por comando (decisión del operador, 2026-10-06: «que los
botones de la UI no sirvan y todo se haga por comandos, para evitar que
alguien jugando dañe producción»): los PUT responden 403 con el comando y no
escriben nada. Las garantías de cada cambio viven en `decisions/control.py`
(`test_decisions_control.py`).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales.decisions import control, probe
from src.plugins.chats.api import perception as api

NOW_MS = 1_790_200_000_000
HOUR_MS = 3_600_000
SERVED = "typesafe/jev-1.13-20260917"


def _probe_report(status: str = "ok", *, hours_ago: int = 2) -> dict:
    return {
        "at_ms": NOW_MS - hours_ago * HOUR_MS, "status": status, "models": [SERVED], "cases": 20,
        "ok_rate": 1.0, "pass_rate": 0.95, "p95_ms": 900, "shape_errors": [], "failures": [],
    }


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(control, "_now_ms", lambda: NOW_MS)
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
    assert body["metrics"] == {
        "days": 0, "turns": 0, "fallback_rate": None, "p95_ms": None, "model_changed": 0, "served_model": None,
    }
    assert {c["code"] for c in body["readiness"]["canary"]} >= {"signal_meta_on", "shadow_days", "shadow_p95"}
    assert body["test_numbers_jev"] is False


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("rollout", {"mode": "shadow", "test_numbers": ["wa_573001234567"]}),
        ("capabilities", {"capability": "baja", "mode": "shadow"}),
        ("workflow", {"mode": "canary"}),
    ],
)
def test_changes_from_the_dashboard_are_refused_with_the_command(
    client: TestClient, tmp_path: Path, path: str, body: dict
) -> None:
    res = client.put(f"/api/chats/perception/{path}", json=body)

    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["reason"] == "by_command"
    assert "src.plugins.chats.agent.sales.decisions.control" in detail["command"]
    assert not (tmp_path / "_rollout").exists()


def test_the_terraform_placeholder_is_not_a_key(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER_set_out_of_band")

    body = client.get("/api/chats/perception/rollout").json()

    assert "api_key" in body["can"]["shadow"]


def test_get_reports_the_latest_probe_and_feeds_the_check(client: TestClient, tmp_path: Path) -> None:
    """La sonda diaria de Jev: el panel ve su resumen y el chequeo `probe_ok`
    de canary y encendido se alimenta de ella."""
    probe.write_report(tmp_path, _probe_report())

    body = client.get("/api/chats/perception/rollout").json()

    assert body.get("probe") == {"status": "ok", "at_ms": NOW_MS - 2 * HOUR_MS, "pass_rate": 0.95, "models": [SERVED]}
    by_code = {c["code"]: c for c in body["readiness"]["canary"]}
    assert "probe_ok" in by_code and by_code["probe_ok"]["ok"] is True
    assert "probe_ok" not in body["can"]["canary"] and "probe_ok" not in body["can"]["on"]


def test_without_a_probe_canary_and_on_stay_closed(client: TestClient) -> None:
    body = client.get("/api/chats/perception/rollout").json()

    assert body.get("probe") == {"status": "sin_datos", "at_ms": None, "pass_rate": None, "models": []}
    assert "probe_ok" in body["can"]["canary"] and "probe_ok" in body["can"]["on"]
    assert "probe_ok" not in body["can"]["shadow"]
