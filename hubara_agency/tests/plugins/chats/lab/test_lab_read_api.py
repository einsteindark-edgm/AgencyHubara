"""Lecturas del contrato `lab@v1` (plan §4.3): lo que la sección Laboratorio
muestra, leído SOLO de `runs/<corrida>/` en el S3 del laboratorio.

Con el PR 8 sirven el banco y el hilo real (A0) de cada conversación, las
trazas de cada turno (para el modal), los checks de producción y el resumen
con la forma de Calidad LLM. Los brazos simulados llegan desde el PR 11.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import lab as api
from tests.plugins.chats.lab.test_lab_cases import SID, T0
from tests.plugins.chats.lab.test_lab_publish import RUN, _published


@pytest.fixture
def http(tmp_path: Path, monkeypatch) -> TestClient:
    store = _published(tmp_path)
    store.put_bytes(f"runs/{RUN}/progress.json", json.dumps({
        "run_id": RUN, "phase": "done", "turns_done": 2, "turns_total": 2, "spent_usd": 0.0,
        "started_at_ms": T0, "updated_at_ms": T0 + 1000, "notes": ["x"],
    }).encode())
    store.put_bytes("runs/run-20260922-dead/progress.json", json.dumps({
        "run_id": "run-20260922-dead", "phase": "failed", "error": "la caja no prendió", "started_at_ms": T0 - 86_400_000,
    }).encode())
    monkeypatch.setattr(api, "get_lab_store", lambda: store)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    return TestClient(app)


def test_runs_list_newest_first_with_phase_spend_and_versions(http) -> None:
    runs = http.get("/api/chats/lab/runs").json()["runs"]

    assert [r["run_id"] for r in runs] == [RUN, "run-20260922-dead"]
    first, failed = runs
    assert (first["phase"], first["bench_id"], first["arms"]) == ("done", "bench-x", ["A0", "A1", "B"])
    assert "registry_version" in first
    assert (failed["phase"], failed["error"]) == ("failed", "la caja no prendió")


def test_bench_report(http) -> None:
    report = http.get(f"/api/chats/lab/runs/{RUN}/bench").json()

    assert report["counts"]["cases"] == 2
    assert report["exclusions"][0]["reason"] == "turno_del_sistema"


def test_conversations_list(http) -> None:
    rows = http.get(f"/api/chats/lab/runs/{RUN}/conversations").json()["conversations"]

    assert rows[0]["session_id"] == SID and rows[0]["verdicts"]["A0"]["ep_001"] == "ALERTA"


def test_thread_of_one_episode_with_the_output_of_each_arm(http) -> None:
    thread = http.get(f"/api/chats/lab/runs/{RUN}/conversations/{SID}", params={"episode": "ep_001"}).json()

    assert [t["turn"] for t in thread["turns"]] == [1, 2]
    assert all(t["episode_id"] == "ep_001" for t in thread["turns"])
    assert thread["turns"][1]["outputs"]["A0"]["sent_texts"] == ["Claro, el envío sale en 12.900"]
    # los mensajes del episodio 2 (desde T0 + 1 h) quedan fuera
    assert all(m["content"] != "ok gracias" for m in thread["messages"])


def test_turn_trace_for_the_modal_synthesizes_steps_for_v1_traces(http) -> None:
    trace = http.get(
        f"/api/chats/lab/runs/{RUN}/conversations/{SID}/turns/trace",
        params={"turn_key": "run:abc/t:2", "arm": "A0", "rep": 0},
    ).json()

    assert trace["fidelity"] == "v1"
    kinds = [s["kind"] for s in trace["steps"]]
    assert kinds == ["inbound", "tool", "outbound"]
    assert trace["steps"][1]["name"] == "send_shipping_rates"
    assert trace["steps"][2]["bubbles"][0]["text"] == "Claro, el envío sale en 12.900"


def test_turn_trace_unknown_is_404(http) -> None:
    resp = http.get(f"/api/chats/lab/runs/{RUN}/conversations/{SID}/turns/trace", params={"turn_key": "nope", "arm": "A0"})

    assert resp.status_code == 404


def test_evaluations_of_a_conversation(http) -> None:
    data = http.get(f"/api/chats/lab/runs/{RUN}/conversations/{SID}/evaluations", params={"arm": "A0"}).json()

    [episode] = data["episodes"]
    est08 = next(r for r in episode["results"] if r["check_id"] == "EST-08")
    assert (est08["verdict"], est08["turn"]) == ("falla", 2)


def test_summary_has_the_quality_chart_shape_and_pending_arms_say_so(http) -> None:
    a0 = http.get(f"/api/chats/lab/runs/{RUN}/summary", params={"arm": "A0"}).json()
    pending = http.get(f"/api/chats/lab/runs/{RUN}/summary", params={"arm": "B"})

    assert {"episodes", "verdicts", "pareto", "trend", "funnel"} <= set(a0)
    assert pending.status_code == 404
    assert "todavía" in pending.json()["detail"]


def test_diff_needs_both_arms(http) -> None:
    assert http.get(f"/api/chats/lab/runs/{RUN}/diff", params={"base": "A1", "cand": "B"}).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/chats/lab/runs/..%2F..%2Fetc/bench",
        "/api/chats/lab/runs/RUN!/bench",
        f"/api/chats/lab/runs/{RUN}/conversations/..%2Fx",
        f"/api/chats/lab/runs/{RUN}/summary?arm=Z",
    ],
)
def test_ids_are_validated(http, path: str) -> None:
    assert http.get(path).status_code in (404, 422)


def test_unknown_run_is_404(http) -> None:
    assert http.get("/api/chats/lab/runs/run-20200101-0000/bench").status_code == 404


def test_the_runs_list_never_walks_every_object(http, monkeypatch) -> None:
    store = api.get_lab_store()
    walked: list[str] = []
    real = store.list_keys
    monkeypatch.setattr(store, "list_keys", lambda prefix: walked.append(prefix) or real(prefix))

    assert len(http.get("/api/chats/lab/runs").json()["runs"]) == 2
    assert "runs/" not in walked


def test_a_run_that_stopped_reporting_is_marked_stale(http, monkeypatch) -> None:
    """La caja se cayó a mitad: la corrida no puede quedar "Corriendo" para
    siempre; tampoco una terminada se marca."""
    store = api.get_lab_store()
    store.put_bytes("runs/run-20260923-live/progress.json", json.dumps({
        "run_id": "run-20260923-live", "phase": "simulating", "started_at_ms": T0, "updated_at_ms": T0,
    }).encode())
    monkeypatch.setattr(api, "_now_ms", lambda: T0 + 2 * 3_600_000)

    runs = {r["run_id"]: r for r in http.get("/api/chats/lab/runs").json()["runs"]}

    assert runs["run-20260923-live"]["stale"] is True
    assert runs[RUN]["stale"] is False and runs["run-20260922-dead"]["stale"] is False
