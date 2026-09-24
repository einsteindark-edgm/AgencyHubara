"""Calidad LLM: filtro "bot actual / bot nuevo" en Producción (plan del
laboratorio PR 18). Durante el canary, las mismas gráficas separan los
episodios que respondió el bot nuevo (algún turno con modo `on` o `canary`
en la traza) de los que respondió el actual (sin modo o en sombra: en sombra
el bot actual es el que contesta)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.api import evals as evals_api
from src.plugins.chats.api import scorecards as api
from src.plugins.chats.shared import turn_traces

NEW = "wa_100000000001"
OLD = "wa_100000000002"
SHADOW = "wa_100000000003"


def test_the_bot_of_an_episode_comes_from_its_traces() -> None:
    assert episode_bot([{"mode": "off"}, {"mode": "on"}]) == "nuevo"
    assert episode_bot([{"mode": "canary"}]) == "nuevo"
    assert episode_bot([{"mode": "shadow"}, {}]) == "actual"
    assert episode_bot([]) == "actual"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "get_vault_dir", lambda: tmp_path)
    cards = store.scorecards_dir(tmp_path)
    for sid, mode, verdict in ((NEW, "on", "PASA"), (OLD, None, "FALLA"), (SHADOW, "shadow", "ALERTA")):
        trace = {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": 1}
        if mode:
            trace["mode"] = mode
        turn_traces.append_trace(tmp_path, sid, trace)
        store.append_scorecard(cards, {"session_id": sid, "episode_id": "ep_001", "verdict": verdict,
                                       "stage_final": "descubrimiento", "results": []})
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")
    return TestClient(app)


def test_the_list_says_which_bot_answered_and_filters(client: TestClient) -> None:
    rows = client.get("/api/chats/evals/scorecards", params={"days": 7}).json()["scorecards"]
    assert {r["session_id"]: r["bot"] for r in rows} == {NEW: "nuevo", OLD: "actual", SHADOW: "actual"}

    only_new = client.get("/api/chats/evals/scorecards", params={"days": 7, "bot": "nuevo"}).json()
    assert [r["session_id"] for r in only_new["scorecards"]] == [NEW] and only_new["count"] == 1


def test_the_charts_filter_by_bot(client: TestClient) -> None:
    current = client.get("/api/chats/evals/checks/stats", params={"days": 14, "bot": "actual"}).json()
    assert current["episodes"] == 2 and current["verdicts"]["FALLA"] == 1 and current["bot"] == "actual"

    everyone = client.get("/api/chats/evals/checks/stats", params={"days": 14}).json()
    assert everyone["episodes"] == 3 and everyone["bot"] is None


def test_an_unknown_bot_is_422(client: TestClient) -> None:
    assert client.get("/api/chats/evals/checks/stats", params={"bot": "otro"}).status_code == 422
