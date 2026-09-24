"""Cast de Calidad LLM con el filtro "bot actual / bot nuevo" (plan del
laboratorio PR 18): `/api/agents/evals/*` reenvía `bot` al contrato de chats."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.agents_admin.api import router
from src.sdk import castkit
from tests.plugins.agents_admin.test_perception_rollout_cast import _Fake


def _client(monkeypatch, capture: dict[str, Any]) -> TestClient:
    monkeypatch.setenv("CHATS_API_BASE", "http://chats.internal:8000")
    monkeypatch.setattr(castkit.httpx, "AsyncClient", lambda **_: _Fake(result=httpx.Response(200, json={}), capture=capture))
    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "provider"),
    [("/api/agents/evals/checks/stats", "/api/chats/evals/checks/stats"), ("/api/agents/evals/scorecards", "/api/chats/evals/scorecards")],
)
def test_the_bot_filter_reaches_the_chats_contract(monkeypatch, path: str, provider: str) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, capture)

    res = client.get(path, params={"days": 14, "bot": "nuevo"})

    assert res.status_code == 200
    assert capture["url"].endswith(provider)
    assert capture["params"] == {"days": 14, "bot": "nuevo"}


def test_without_bot_the_param_does_not_travel(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    _client(monkeypatch, capture).get("/api/agents/evals/checks/stats", params={"days": 14})

    assert capture["params"] == {"days": 14}


def test_an_unknown_bot_never_reaches_the_provider(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    res = _client(monkeypatch, capture).get("/api/agents/evals/checks/stats", params={"bot": "otro"})

    assert res.status_code == 422 and capture == {}
