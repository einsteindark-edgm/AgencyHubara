"""Calidad LLM con la vista del laboratorio, sobre producción (2026-10-02):
`/api/agents/evals/production/*` reenvía al contrato evals@v1 de chats. La
conversación se valida antes de armar la ruta del provider, y lo que puede
calificar al vuelo (las evaluaciones de una conversación, el informe de Jev)
espera más que el resto (L-1: el plazo se dimensiona por la cadena entera)."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.agents_admin.api import router
from src.sdk import castkit
from tests.plugins.agents_admin.test_perception_rollout_cast import _Fake

SID = "wa_573001234567"


def _client(monkeypatch, capture: dict[str, Any]) -> TestClient:
    monkeypatch.setenv("CHATS_API_BASE", "http://chats.internal:8000")

    def client(**kw):
        capture["timeout"] = kw.get("timeout")
        return _Fake(result=httpx.Response(200, json={}), capture=capture)

    monkeypatch.setattr(castkit.httpx, "AsyncClient", client)
    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "params", "provider", "sent", "slow"),
    [
        ("/evals/production/conversations", {"days": 14, "bot": "nuevo"}, "/evals/production/conversations",
         {"days": 14, "bot": "nuevo"}, False),
        (f"/evals/production/conversations/{SID}", {"episode": "ep_001"}, f"/evals/production/conversations/{SID}",
         {"episode": "ep_001"}, False),
        (f"/evals/production/conversations/{SID}/turns/trace", {"turn_key": f"{SID}/ep_001/t1"},
         f"/evals/production/conversations/{SID}/turns/trace", {"turn_key": f"{SID}/ep_001/t1"}, False),
        (f"/evals/production/conversations/{SID}/evaluations", {}, f"/evals/production/conversations/{SID}/evaluations",
         None, True),
        ("/evals/production/jev", {"days": 28}, "/evals/production/jev", {"days": 28, "bot": "nuevo"}, True),
    ],
)
def test_each_route_reaches_the_chats_contract(monkeypatch, path, params, provider, sent, slow) -> None:
    capture: dict[str, Any] = {}

    res = _client(monkeypatch, capture).get(f"/api/agents{path}", params=params)

    assert res.status_code == 200
    assert capture["url"] == f"http://chats.internal:8000/api/chats{provider}"
    assert (capture["params"] or None) == sent
    assert (capture["timeout"] > 15) is slow


def test_a_bad_conversation_never_reaches_the_provider(monkeypatch) -> None:
    capture: dict[str, Any] = {}

    res = _client(monkeypatch, capture).get("/api/agents/evals/production/conversations/..%2Fadmin")

    assert res.status_code in (404, 422) and "url" not in capture
