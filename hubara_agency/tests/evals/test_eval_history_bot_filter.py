"""Calidad LLM: la «Tendencia de calidad» por bot (operador, 2026-10-08).

La tendencia del eval por promedio se ve en el Resumen y en la sección de
cada bot (Botsito = el workflow actual, Colossus = el bot Jev): la serie y
sus conversaciones se filtran con el mismo criterio que la matriz — qué
workflow respondió los turnos del cliente de ese episodio.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales_eval.evals import history
from src.plugins.chats.api import evals as evals_api
from src.plugins.chats.api import scorecards as scorecards_api
from src.plugins.chats.shared import turn_traces

COLOSSUS = "wa_100000000001"
BOTSITO = "wa_100000000002"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    hist = tmp_path / "_evals" / "history"
    monkeypatch.setattr(evals_api, "get_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(scorecards_api, "get_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(evals_api, "get_eval_history_dir", lambda: hist)
    monkeypatch.setattr(evals_api, "_candidate_index", lambda: {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for sid, workflow, score in ((COLOSSUS, "v2", 0.9), (BOTSITO, "v1", 0.4)):
        turn_traces.append_trace(tmp_path, sid, {"turn": 1, "episode_id": "ep_001", "trigger": "customer",
                                                 "workflow": workflow})
        history.append_history_record(hist, run_date=today, session_id=sid, episode_id="ep_001", suite="online",
                                      scores=[("tono", score, score >= 0.7, "")])
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")
    return TestClient(app)


def _avg(body: dict) -> float:
    (series,) = body["series"]
    return series["points"][0]["avg"]


def test_the_quality_trend_filters_by_bot(client: TestClient) -> None:
    everyone = client.get("/api/chats/evals/history", params={"days": 7}).json()
    assert _avg(everyone) == 0.65 and everyone["bot"] is None

    colossus = client.get("/api/chats/evals/history", params={"days": 7, "bot": "nuevo"}).json()
    assert _avg(colossus) == 0.9 and colossus["bot"] == "nuevo"

    assert _avg(client.get("/api/chats/evals/history", params={"days": 7, "bot": "actual"}).json()) == 0.4


def test_the_clients_of_the_trend_filter_by_bot(client: TestClient) -> None:
    body = client.get("/api/chats/evals/conversations", params={"days": 7, "bot": "actual"}).json()

    assert [c["session_id"] for c in body["conversations"]] == [BOTSITO] and body["bot"] == "actual"
    assert body["count"] == 1


def test_without_a_bot_the_trend_does_not_read_traces(client: TestClient, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("no debería leer trazas")

    monkeypatch.setattr(turn_traces, "read_traces", boom)

    assert client.get("/api/chats/evals/history", params={"days": 7}).status_code == 200
    assert client.get("/api/chats/evals/conversations", params={"days": 7}).status_code == 200


def test_an_unknown_bot_is_422(client: TestClient) -> None:
    assert client.get("/api/chats/evals/history", params={"bot": "otro"}).status_code == 422
    assert client.get("/api/chats/evals/conversations", params={"bot": "otro"}).status_code == 422
