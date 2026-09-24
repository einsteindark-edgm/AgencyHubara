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


def test_the_bot_of_an_episode_comes_from_its_customer_turns() -> None:
    assert episode_bot([{"mode": "canary"}]) == "nuevo"
    assert episode_bot([{"mode": "shadow"}, {}]) == "actual"
    assert episode_bot([]) == "actual"
    # El complemento y el ghosting corren sin capas (`mode: off`): no dicen
    # qué bot respondió al cliente.
    assert episode_bot([{"mode": "on", "trigger": "customer"}, {"mode": "off", "trigger": "complement"},
                        {"mode": "off", "trigger": "ghost"}]) == "nuevo"


def test_an_episode_answered_by_both_bots_is_mixed() -> None:
    """Al subir o bajar de etapa, un episodio en curso queda con turnos de los
    dos bots: no se le carga a ninguno (solo aparece en "Todos")."""
    assert episode_bot([{"mode": "shadow", "trigger": "customer"}, {"mode": "on", "trigger": "customer"}]) == "mixto"
    assert episode_bot([{"mode": "on", "trigger": "customer"}, {"mode": "off", "trigger": "customer"}]) == "mixto"


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


def test_the_list_filters_by_bot(client: TestClient) -> None:
    everyone = client.get("/api/chats/evals/scorecards", params={"days": 7}).json()
    assert {r["session_id"] for r in everyone["scorecards"]} == {NEW, OLD, SHADOW}

    only_new = client.get("/api/chats/evals/scorecards", params={"days": 7, "bot": "nuevo"}).json()
    assert [r["session_id"] for r in only_new["scorecards"]] == [NEW] and only_new["count"] == 1
    assert only_new["scorecards"][0]["bot"] == "nuevo"


def test_without_a_filter_the_traces_are_not_read(client: TestClient, monkeypatch) -> None:
    """La lista de Calidad LLM se pide a 56 días: sin filtro no hay por qué
    releer las trazas de cada episodio (el cast corta a los 15 s)."""
    def boom(*_a, **_k):
        raise AssertionError("no debería leer trazas")

    monkeypatch.setattr(turn_traces, "read_traces", boom)

    assert client.get("/api/chats/evals/scorecards", params={"days": 7}).status_code == 200
    assert client.get("/api/chats/evals/checks/stats", params={"days": 14}).status_code == 200


def test_the_traces_of_a_session_are_read_once(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    cards = store.scorecards_dir(tmp_path)
    turn_traces.append_trace(tmp_path, NEW, {"turn": 2, "episode_id": "ep_002", "trigger": "customer", "mode": "on"})
    store.append_scorecard(cards, {"session_id": NEW, "episode_id": "ep_002", "verdict": "PASA",
                                   "stage_final": "descubrimiento", "results": []})
    calls: list[str] = []
    real = turn_traces.read_traces
    monkeypatch.setattr(turn_traces, "read_traces", lambda vault, sid: calls.append(sid) or real(vault, sid))

    body = client.get("/api/chats/evals/scorecards", params={"days": 7, "bot": "nuevo"}).json()

    assert body["count"] == 2 and calls.count(NEW) == 1


def test_a_mixed_episode_counts_for_neither_bot(client: TestClient, tmp_path: Path) -> None:
    mixed = "wa_100000000004"
    for mode in ("shadow", "on"):
        turn_traces.append_trace(tmp_path, mixed, {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "mode": mode})
    store.append_scorecard(store.scorecards_dir(tmp_path), {"session_id": mixed, "episode_id": "ep_001",
                                                            "verdict": "FALLA", "stage_final": "descubrimiento", "results": []})

    ids = lambda bot: {r["session_id"] for r in client.get("/api/chats/evals/scorecards", params={"days": 7, "bot": bot}).json()["scorecards"]}  # noqa: E731

    assert mixed not in ids("actual") and mixed not in ids("nuevo")


def test_a_trace_file_with_broken_utf8_does_not_break_quality(client: TestClient, tmp_path: Path) -> None:
    """Un corte a mitad de escritura (disco lleno) deja bytes que no son UTF-8:
    Calidad LLM sigue respondiendo."""
    path = turn_traces.trace_path(tmp_path, NEW)
    path.write_bytes(path.read_bytes() + b'{"turn": 9, "text": "\xc3"}\n')

    res = client.get("/api/chats/evals/checks/stats", params={"days": 14, "bot": "nuevo"})

    assert res.status_code == 200 and res.json()["episodes"] == 1


def test_the_charts_filter_by_bot(client: TestClient) -> None:
    current = client.get("/api/chats/evals/checks/stats", params={"days": 14, "bot": "actual"}).json()
    assert current["episodes"] == 2 and current["verdicts"]["FALLA"] == 1 and current["bot"] == "actual"

    everyone = client.get("/api/chats/evals/checks/stats", params={"days": 14}).json()
    assert everyone["episodes"] == 3 and everyone["bot"] is None


def test_an_unknown_bot_is_422(client: TestClient) -> None:
    assert client.get("/api/chats/evals/checks/stats", params={"bot": "otro"}).status_code == 422
