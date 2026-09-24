"""Cast agents_admin→chats del encendido del bot nuevo (plan del laboratorio
PR 16): el panel de Agents solo habla con `/api/agents/perception/rollout`;
el cast reenvía al contrato `perception-rollout@v1` de chats con el
Authorization del operador y con los fallos honestos de castkit (L-1)."""
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
            self._capture.update(method=method, url=url, json=json, headers=headers)
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


def test_put_forwards_the_body_and_a_422_passes_through(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(422, json={"detail": {"reason": "not_ready", "failing": ["shadow_days"]}}),
                     capture=capture)

    res = client.put("/api/agents/perception/rollout", json={"mode": "on"})

    assert capture["method"] == "PUT" and capture["json"] == {"mode": "on"}
    assert res.status_code == 422 and res.json()["detail"]["failing"] == ["shadow_days"]


def test_a_timeout_is_504_outcome_unknown(monkeypatch) -> None:
    client = _client(monkeypatch, exc=httpx.ReadTimeout("read"))

    res = client.put("/api/agents/perception/rollout", json={"mode": "off"})

    assert res.status_code == 504 and "PUEDE haberse aplicado" in res.json()["detail"]
