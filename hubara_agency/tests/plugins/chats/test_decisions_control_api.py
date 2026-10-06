"""Control del motor de decisiones por capacidad y por versión del workflow
(diseño v2 §08 y §11, fase F7), en el mismo contrato `perception-rollout@v1`
(solo crece: el panel viejo sigue leyendo lo que leía).

* GET agrega `capabilities` (modo, techo, vara por modo) y `workflow_v2`.
* PUT /perception/capabilities {capability, mode}: cada capacidad con su
  interruptor dentro del techo de Terraform `SALES_CAPABILITIES_CEILING`;
  subir exige su vara (7 días en sombra, caídas < 1 %, p95 < 1,5 s,
  desacuerdos calificados con Jev ganando); bajar siempre pasa.
* PUT /perception/workflow {mode}: V2 por números de prueba y porcentaje
  (canary) antes de todos (on), dentro de `SALES_WORKFLOW_V2_CEILING`; no se
  salta de apagado a encendido. Bajar siempre pasa (vuelta atrás).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.api import perception as api

NOW_MS = 1_790_200_000_000


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "_now_ms", lambda: NOW_MS)
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    monkeypatch.setenv("SALES_WORKFLOW_V2_CEILING", "on")
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    return TestClient(app)


def test_get_lists_every_capability_and_the_workflow_version(client: TestClient) -> None:
    body = client.get("/api/chats/perception/rollout").json()

    caps = body["capabilities"]
    assert {"compra", "retoma", "baja", "persona", "enumeracion", "monto", "selector"} <= set(caps)
    assert caps["baja"]["mode"] == "off" and caps["baja"]["ceiling"] == "on"
    assert caps["baja"]["can"]["shadow"] == []
    assert "shadow_days" in caps["baja"]["can"]["canary"]
    assert body["workflow_v2"]["mode"] == "off" and body["workflow_v2"]["ceiling"] == "on"
    assert body["workflow_v2"]["can"]["canary"] == []
    assert "staged" in body["workflow_v2"]["can"]["on"]


def test_a_capability_moves_to_shadow_within_the_ceiling(client: TestClient, tmp_path: Path) -> None:
    res = client.put("/api/chats/perception/capabilities", json={"capability": "baja", "mode": "shadow"})

    assert res.status_code == 200, res.text
    assert res.json()["capabilities"]["baja"]["mode"] == "shadow"
    assert bots.bot_for_session("wa_573009876543", vault_dir=tmp_path).provider("baja") == "sombra"


def test_raising_a_capability_without_its_bar_is_refused(client: TestClient) -> None:
    res = client.put("/api/chats/perception/capabilities", json={"capability": "baja", "mode": "canary"})

    assert res.status_code == 422
    assert "shadow_days" in res.json()["detail"]["failing"]


def test_a_capability_above_the_ceiling_is_refused(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "off")

    res = client.put("/api/chats/perception/capabilities", json={"capability": "baja", "mode": "shadow"})

    assert res.status_code == 422 and "within_ceiling" in res.json()["detail"]["failing"]


def test_lowering_a_capability_always_passes(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    bots.write_capability_modes(tmp_path, {"compra": "on"})
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "off")

    res = client.put("/api/chats/perception/capabilities", json={"capability": "compra", "mode": "off"})

    assert res.status_code == 200 and res.json()["capabilities"]["compra"]["mode"] == "off"


def test_an_unknown_capability_or_mode_is_refused(client: TestClient) -> None:
    assert client.put("/api/chats/perception/capabilities", json={"capability": "juguete", "mode": "shadow"}).status_code == 422
    assert client.put("/api/chats/perception/capabilities", json={"capability": "baja", "mode": "turbo"}).status_code == 422


def test_workflow_v2_goes_to_canary_before_everyone(client: TestClient, tmp_path: Path) -> None:
    assert client.put("/api/chats/perception/workflow", json={"mode": "on"}).status_code == 422

    res = client.put("/api/chats/perception/workflow", json={"mode": "canary"})

    assert res.status_code == 200, res.text
    assert res.json()["workflow_v2"]["mode"] == "canary"
    assert bots.read_decisions_state(tmp_path)["workflow_v2"] == "canary"
    assert client.put("/api/chats/perception/workflow", json={"mode": "on"}).status_code == 200


def test_workflow_v2_respects_the_ceiling_and_always_goes_back(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_WORKFLOW_V2_CEILING", "off")
    assert client.put("/api/chats/perception/workflow", json={"mode": "canary"}).status_code == 422

    bots.write_workflow_mode(tmp_path, "on")
    res = client.put("/api/chats/perception/workflow", json={"mode": "off"})

    assert res.status_code == 200 and res.json()["workflow_v2"]["mode"] == "off"
    assert client.put("/api/chats/perception/workflow", json={"mode": "shadow"}).status_code == 422
