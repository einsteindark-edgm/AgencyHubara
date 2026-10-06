"""Control del motor de decisiones por capacidad y por versión del workflow
(diseño v2 §08 y §11, fase F7), en el mismo contrato `perception-rollout@v1`
(solo crece: el panel viejo sigue leyendo lo que leía).

* GET agrega `capabilities` (modo, techo, vara por modo) y `workflow_v2`.
* Los cambios (capacidad, workflow) van solo por comando desde el
  2026-10-06: `decisions/control.py` y `test_decisions_control.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales.decisions import control
from src.plugins.chats.api import perception as api

NOW_MS = 1_790_200_000_000


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(control, "_now_ms", lambda: NOW_MS)
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
