"""Cast agents_admin→chats del encendido del bot nuevo (plan del laboratorio
PR 16): el panel de Agents solo habla con `/api/agents/perception/rollout`;
el cast reenvía al contrato `perception-rollout@v1` de chats con el
Authorization del operador y con los fallos honestos de castkit (L-1). Solo
lee: los cambios van por comando (2026-10-06)."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.agents_admin.api import router
from src.sdk import castkit


class _Fake:
    def __init__(self, *, result=None, exc=None, capture=None) -> None:
        self._result, self._exc, self._capture = result, exc, capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, *, params=None, json=None, headers=None, files=None):
        if self._capture is not None:
            self._capture.update(method=method, url=url, json=json, headers=headers, params=params)
        if self._exc is not None:
            raise self._exc
        return self._result


def _client(monkeypatch, **kw) -> TestClient:
    monkeypatch.setenv("CHATS_API_BASE", "http://chats.internal:8000")
    monkeypatch.setattr(castkit.httpx, "AsyncClient", lambda **_: _Fake(**kw))
    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    return TestClient(app)


def test_get_forwards_to_the_chats_contract(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={"state": {"mode": "off"}}), capture=capture)

    res = client.get("/api/agents/perception/rollout", headers={"Authorization": "Bearer operador"})

    assert res.status_code == 200 and res.json() == {"state": {"mode": "off"}}
    assert capture["url"] == "http://chats.internal:8000/api/chats/perception/rollout"
    assert capture["headers"]["Authorization"] == "Bearer operador"


def test_the_cast_only_reads_changes_go_by_command(monkeypatch) -> None:
    """Desde el 2026-10-06 el bot nuevo se cambia solo por comando
    (`decisions/control.py`): el cast no tiene PUT y nada llega a chats."""
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={}), capture=capture)

    for path, body in (
        ("rollout", {"mode": "on"}),
        ("capabilities", {"capability": "baja", "mode": "shadow"}),
        ("workflow", {"mode": "canary"}),
    ):
        assert client.put(f"/api/agents/perception/{path}", json=body).status_code in (404, 405)

    assert capture == {}


def test_a_timeout_is_504(monkeypatch) -> None:
    client = _client(monkeypatch, exc=httpx.ReadTimeout("read"))

    assert client.get("/api/agents/perception/rollout").status_code == 504


def test_the_decision_engine_reaches_the_chats_contract(monkeypatch) -> None:
    """La pestaña «Motor de decisiones» de Calidad LLM (2026-10-02)."""
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={"bundle": {"ref": "ventas@1"}}), capture=capture)

    res = client.get("/api/agents/perception/engine")

    assert res.status_code == 200 and res.json() == {"bundle": {"ref": "ventas@1"}}
    assert capture["url"] == "http://chats.internal:8000/api/chats/perception/engine"
