"""Cast agents_admin→chats de la frecuencia del remarketing
(`remarketing-frequency@v1`): el panel de Agents solo habla con
`/api/agents/remarketing/frequency`; el cast reenvía (GET y PUT) al contrato
de chats con el Authorization del operador y con los fallos honestos de
castkit (L-1: un timeout en el PUT es 504 «PUEDE haberse aplicado»)."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.agents_admin.api import router
from src.sdk import castkit

PATH = "/api/agents/remarketing/frequency"


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


def test_get_reenvia_al_contrato_de_chats(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={"max_touches": 5}), capture=capture)

    res = client.get(PATH, headers={"Authorization": "Bearer operador"})

    assert res.status_code == 200 and res.json() == {"max_touches": 5}
    assert capture["method"] == "GET"
    assert capture["url"] == "http://chats.internal:8000/api/chats/remarketing/frequency"
    assert capture["headers"]["Authorization"] == "Bearer operador"


def test_put_reenvia_el_cuerpo_y_la_identidad_del_operador(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={"max_touches": 2}), capture=capture)

    res = client.put(PATH, json={"max_touches": 2}, headers={"Authorization": "Bearer operador"})

    assert res.status_code == 200 and res.json() == {"max_touches": 2}
    assert capture["method"] == "PUT"
    assert capture["url"] == "http://chats.internal:8000/api/chats/remarketing/frequency"
    assert capture["json"] == {"max_touches": 2}
    assert capture["headers"]["Authorization"] == "Bearer operador"


def test_put_timeout_es_504_porque_puede_haberse_aplicado(monkeypatch) -> None:
    client = _client(monkeypatch, exc=httpx.ReadTimeout("lento"))

    res = client.put(PATH, json={"max_touches": 2})

    assert res.status_code == 504
