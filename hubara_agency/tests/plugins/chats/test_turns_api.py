"""Chats: el botón por turno y su hilo (plan del laboratorio PR 17).

  GET /api/chats/sessions/{sesión}/turns               índice de turnos de la traza
  GET /api/chats/sessions/{sesión}/turns/trace?turn_key=  el hilo de UN turno
  GET /api/dashboard/sessions/{sesión}                 cada burbuja con su `turn_key`

El `turn_key` va como query: lleva `/` y no viaja en un segmento de ruta.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.chats.api.dashboard as dash_mod
from src.plugins.chats.api import turns as turns_api

SID = "wa_573001234567"
T0 = 1_790_200_000_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    s = tmp_path / SID
    (s / "evals").mkdir(parents=True)
    (s / "sessions").mkdir()
    traces = [
        {"turn": 2, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 70_000, "turn_key": "run:x/t:2",
         "mode": "shadow", "inbound": [{"seq": 1, "wamid": "wamid.B", "text": "catálogo"}], "inbound_text": "catálogo",
         "sent_texts": ["Te dejo el catálogo"], "steps": [{"i": 1, "at_ms": 10, "kind": "llm"}]},
        {"turn": 1, "episode_id": "ep_001", "trigger": "customer", "turn_started_ms": T0 + 2_000,
         "inbound_text": "hola", "sent_texts": ["¡Hola!"], "tools": [], "guards": []},
    ]
    (s / "evals" / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces) + "\n", encoding="utf-8")
    events = [
        {"role": "user", "content": "hola", "timestamp": _iso(T0)},
        {"role": "assistant", "content": "¡Hola!", "timestamp": _iso(T0 + 6_000)},
        {"role": "user", "content": "catálogo", "timestamp": _iso(T0 + 60_000), "wamid": "wamid.B"},
        {"role": "assistant", "content": "Te dejo el catálogo", "timestamp": _iso(T0 + 75_000)},
    ]
    (s / "sessions" / f"{SID}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    monkeypatch.setattr(turns_api, "_vault_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(turns_api.router, prefix="/api/chats")
    app.include_router(dash_mod.router, prefix="/api/dashboard")
    with patch.object(dash_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app)


def test_the_turn_index_lists_each_turn_in_order(client: TestClient) -> None:
    turns = client.get(f"/api/chats/sessions/{SID}/turns").json()["turns"]

    assert [t["turn_key"] for t in turns] == [f"{SID}/ep_001/t1", "run:x/t:2"]
    assert (turns[1]["fidelity"], turns[1]["mode"], turns[1]["trigger"]) == ("v2", "shadow", "customer")
    assert turns[0]["fidelity"] == "v1"


def test_the_thread_of_one_turn(client: TestClient) -> None:
    body = client.get(f"/api/chats/sessions/{SID}/turns/trace", params={"turn_key": "run:x/t:2"}).json()

    assert body["fidelity"] == "v2"
    assert [s["kind"] for s in body["steps"]] == ["inbound", "llm"]


def test_an_unknown_turn_is_404_and_a_bad_session_is_refused(client: TestClient) -> None:
    assert client.get(f"/api/chats/sessions/{SID}/turns/trace", params={"turn_key": "run:x/t:9"}).status_code == 404
    assert client.get("/api/chats/sessions/..%2Fetc/turns").status_code in (400, 404, 422)
    assert client.get("/api/chats/sessions/wa_573007654321/turns").json() == {"turns": []}


def test_each_bubble_of_the_chat_carries_its_turn(client: TestClient) -> None:
    messages = client.get(f"/api/dashboard/sessions/{SID}").json()["messages"]

    assert [m.get("turn_key") for m in messages] == [f"{SID}/ep_001/t1", f"{SID}/ep_001/t1", "run:x/t:2", "run:x/t:2"]


def _traces_file(tmp_path: Path) -> Path:
    return tmp_path / SID / "evals" / "turn_traces.jsonl"


def test_a_trace_file_with_broken_bytes_does_not_break_the_chat(client: TestClient, tmp_path: Path) -> None:
    """Un corte a mitad de escritura (o un byte raro) en la traza no puede
    dejar sin historial el chat de producción ni sin índice sus turnos."""
    with _traces_file(tmp_path).open("ab") as f:
        f.write(b'{"turn": 3, "inbound_text": "\xff\xfe roto"}\n')

    history = client.get(f"/api/dashboard/sessions/{SID}")
    turns = client.get(f"/api/chats/sessions/{SID}/turns")

    assert history.status_code == 200 and len(history.json()["messages"]) == 4
    assert turns.status_code == 200 and len(turns.json()["turns"]) == 3


def test_a_failure_annotating_turns_leaves_the_chat_without_buttons_not_broken(client: TestClient, monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("traza inesperada")

    monkeypatch.setattr(dash_mod, "annotate_turn_keys", boom)

    response = client.get(f"/api/dashboard/sessions/{SID}")

    assert response.status_code == 200
    assert all("turn_key" not in m for m in response.json()["messages"])


def test_a_trace_with_a_bad_start_time_does_not_break_the_turn_index(client: TestClient, tmp_path: Path) -> None:
    with _traces_file(tmp_path).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"turn": "3a", "episode_id": "ep_001", "turn_started_ms": "ayer", "inbound_text": "x"}) + "\n")

    response = client.get(f"/api/chats/sessions/{SID}/turns")

    assert response.status_code == 200 and len(response.json()["turns"]) == 3
