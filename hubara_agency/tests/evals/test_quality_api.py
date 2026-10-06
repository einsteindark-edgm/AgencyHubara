"""Calidad LLM: la API de la vista del laboratorio sobre producción
(evals@v1, 2026-10-02). La consume Agents por su cast (`/api/agents/evals/
production/*`); las formas son las del laboratorio (sin brazos ni
repeticiones) para que la misma vista muestre las dos cosas.

  GET /api/chats/evals/production/conversations?days&bot      la lista
  GET /api/chats/evals/production/conversations/{sid}?episode  el hilo
  GET …/{sid}/turns/trace?turn_key                              la ventana del turno
  GET …/{sid}/evaluations                                       cada episodio turno por turno
  GET /api/chats/evals/production/jev?days&bot                  el informe de Jev
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales_eval.scorecard import catalog_context, store
from src.plugins.chats.api import evals as evals_api
from src.plugins.chats.api import quality as api
from tests.evals.scorecard.incidents import CATALOG_CTX
from tests.evals.test_quality_view import SID, _decisions, _ms, _seed

TODAY = datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    _seed(tmp_path)
    monkeypatch.setattr(api, "get_vault_dir", lambda: tmp_path)

    async def ctx(catalog=None):
        return CATALOG_CTX

    monkeypatch.setattr(catalog_context, "build_check_context", ctx)
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")
    return TestClient(app)


def _card(vault: Path, **over) -> None:
    store.append_scorecard(store.scorecards_dir(vault), {
        "session_id": SID, "episode_id": "ep_001", "verdict": "ALERTA", "episode_date": TODAY, "mode": "turn",
        "by_turn": [{"turn": 1, "verdict": "PASA", "results": []}, {"turn": 3, "verdict": "ALERTA", "results": []}],
        "results": [], "registry_version": 999, **over,
    })


def test_the_list_has_each_conversation_with_its_episodes(client: TestClient, tmp_path: Path) -> None:
    _card(tmp_path)

    body = client.get("/api/chats/evals/production/conversations", params={"days": 7}).json()

    [row] = body["conversations"]
    assert (row["session_id"], row["verdicts"], row["bots"], row["turns"]) == (SID, {"ep_001": "ALERTA"}, {"ep_001": "nuevo"}, 2)
    assert (body["days"], body["bot"], body["count"]) == (7, None, 1)


def test_the_list_filters_by_bot(client: TestClient, tmp_path: Path) -> None:
    _card(tmp_path)

    jev = client.get("/api/chats/evals/production/conversations", params={"days": 7, "bot": "nuevo"}).json()
    current = client.get("/api/chats/evals/production/conversations", params={"days": 7, "bot": "actual"}).json()

    assert [r["session_id"] for r in jev["conversations"]] == [SID] and current["conversations"] == []
    assert client.get("/api/chats/evals/production/conversations", params={"bot": "otro"}).status_code == 422


def test_the_thread_of_a_conversation_and_of_one_episode(client: TestClient) -> None:
    whole = client.get(f"/api/chats/evals/production/conversations/{SID}").json()
    one = client.get(f"/api/chats/evals/production/conversations/{SID}", params={"episode": "ep_001"}).json()

    assert [t["turn"] for t in whole["turns"]] == [1, 3] and len(whole["messages"]) == 4
    assert one["episode_id"] == "ep_001" and [t["turn"] for t in one["turns"]] == [1, 3]
    assert client.get(f"/api/chats/evals/production/conversations/{SID}", params={"episode": "ep_009"}).status_code == 404
    assert client.get("/api/chats/evals/production/conversations/no-es-sesion").status_code == 422


def test_the_turn_window_brings_the_decisions_of_jev(client: TestClient, tmp_path: Path) -> None:
    _decisions(tmp_path, [{"at_ms": _ms("15:01:02"), "stage": "turno", "capability": "monto", "by": "jev", "provider": "jev"}])

    body = client.get(f"/api/chats/evals/production/conversations/{SID}/turns/trace",
                      params={"turn_key": f"{SID}/ep_001/t3"}).json()

    assert body["fidelity"] == "v2" and body["steps"][0]["kind"] == "inbound"
    assert [(d["capability"], d["stage"]) for d in body["trace"]["decisions"]] == [("monto", "turno")]
    missing = client.get(f"/api/chats/evals/production/conversations/{SID}/turns/trace", params={"turn_key": "otro"})
    assert missing.status_code == 404


def test_each_episode_comes_graded_turn_by_turn(client: TestClient, tmp_path: Path) -> None:
    """Lo guardado en modo turno se sirve tal cual; un episodio sin calificar
    (o calificado con la vista vieja) se califica al vuelo, turno por turno."""
    live = client.get(f"/api/chats/evals/production/conversations/{SID}/evaluations").json()
    _card(tmp_path)
    stored = client.get(f"/api/chats/evals/production/conversations/{SID}/evaluations").json()

    [episode] = live["episodes"]
    assert (episode["episode_id"], episode["mode"]) == ("ep_001", "turn")
    assert [t["turn"] for t in episode["by_turn"]] == [1, 3]
    assert stored["episodes"][0]["registry_version"] == 999


def test_the_jev_report_of_the_window(client: TestClient, tmp_path: Path) -> None:
    _card(tmp_path)

    body = client.get("/api/chats/evals/production/jev", params={"days": 7}).json()

    assert (body["bot"], body["episodes"], body["turns"]) == ("nuevo", 1, 2)
    assert body["perception"]["fallbacks"] == 1
