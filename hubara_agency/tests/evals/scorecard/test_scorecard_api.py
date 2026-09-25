"""API del scorecard (HU-SC-2/3/4) bajo `/api/chats/evals/*` — contrato del
Apéndice A de SALES_SCORECARD_PLAN.md, que consume el dashboard por el cast."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales_eval.scorecard import catalog_context, store
from src.plugins.chats.api import evals as evals_api
from src.plugins.chats.api import scorecards as api
from src.plugins.chats.shared import turn_traces
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix, traces_from

SESSION = "wa_100000000001"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api, "get_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "get_eval_history_dir", lambda: tmp_path / "_evals" / "history")

    async def ctx(catalog=None):
        return CATALOG_CTX

    monkeypatch.setattr(catalog_context, "build_check_context", ctx)
    (tmp_path / SESSION).mkdir()
    (tmp_path / SESSION / "metadata.json").write_text(
        json.dumps({"episodes": [{"episode_id": "ep_007", "closing_tag": "CONFIRMADO_SIN_DATOS"}]}), encoding="utf-8"
    )
    for tr in traces_from(pr281_before_fix()):
        turn_traces.append_trace(tmp_path, SESSION, tr)
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")
    return TestClient(app)


def test_checks_registry_endpoint(client: TestClient) -> None:
    data = client.get("/api/chats/evals/checks").json()

    assert data["registry_version"] >= 1
    assert {"id", "label", "stage"} <= set(data["families"][0])
    con01 = next(c for c in data["checks"] if c["id"] == "CON-01")
    assert (con01["level"], con01["kind"], con01["stage"]) == ("critico", "code", "confirmacion")


def test_scorecard_detail_computes_on_the_fly_when_not_stored(client: TestClient) -> None:
    data = client.get("/api/chats/evals/scorecard", params={"session_id": SESSION, "episode_id": "ep_007"}).json()

    assert data["stored"] is False
    assert data["scorecard"]["verdict"] == "FALLA"
    assert data["trajectory"]["fidelity"] == "trace"
    assert len(data["trajectory"]["turns"]) == 10
    assert data["legacy"] is None


def test_rescore_stores_and_lists_it(client: TestClient, tmp_path: Path) -> None:
    detail = client.post("/api/chats/evals/scorecard/rescore",
                         json={"session_id": SESSION, "episode_id": "ep_007", "judge": False}).json()
    assert detail["stored"] is True

    listing = client.get("/api/chats/evals/scorecards", params={"days": 7}).json()
    assert listing["count"] == 1
    row = listing["scorecards"][0]
    assert (row["session_id"], row["verdict"], row["checks"]["CON-01"]) == (SESSION, "FALLA", "falla")
    assert "results" not in row

    stats = client.get("/api/chats/evals/checks/stats", params={"days": 14}).json()
    assert stats["verdicts"]["FALLA"] == 1
    assert {"CON-01", "TAG-01"} <= {p["check_id"] for p in stats["pareto"]}
    assert stats["funnel"] and stats["trend"]


def test_rescore_without_judge_keeps_previous_judge_results(client: TestClient, tmp_path: Path) -> None:
    store.append_scorecard(store.scorecards_dir(tmp_path), {
        "session_id": SESSION, "episode_id": "ep_007", "verdict": "FALLA", "judge": True,
        "results": [{"check_id": "DES-04", "verdict": "falla", "turn": 3, "evidence": "e", "critique": "c", "source": "judge"}],
    })

    detail = client.post("/api/chats/evals/scorecard/rescore",
                         json={"session_id": SESSION, "episode_id": "ep_007", "judge": False}).json()

    assert detail["scorecard"]["judge"] is True
    assert any(r["check_id"] == "DES-04" and r["source"] == "judge" for r in detail["scorecard"]["results"])


def test_rescore_without_judge_keeps_the_topic_coverage_of_est08(client: TestClient, tmp_path: Path) -> None:
    """EST-08 v2 guarda la cobertura por asunto; recalcular solo el código no la borra."""
    topics = [{"topic": "catálogo", "turn": 2, "msg": 1, "covered": False, "evidence": "me mandas el catálogo"}]
    store.append_scorecard(store.scorecards_dir(tmp_path), {
        "session_id": SESSION, "episode_id": "ep_007", "verdict": "ALERTA", "judge": True,
        "results": [{"check_id": "EST-08", "verdict": "falla", "turn": 2, "evidence": "e", "critique": "c",
                     "source": "judge", "topics": topics}],
    })

    detail = client.post("/api/chats/evals/scorecard/rescore",
                         json={"session_id": SESSION, "episode_id": "ep_007", "judge": False}).json()

    est08 = next(r for r in detail["scorecard"]["results"] if r["check_id"] == "EST-08")
    assert est08["topics"] == topics


def test_rescore_with_judge_queues_the_workflow(client: TestClient, monkeypatch) -> None:
    started: list = []

    async def fake_start(session_id: str, episode_id: str) -> str:
        started.append((session_id, episode_id))
        return "scorecard-wf-1"

    monkeypatch.setattr(api, "_start_judge_workflow", fake_start)

    detail = client.post("/api/chats/evals/scorecard/rescore",
                         json={"session_id": SESSION, "episode_id": "ep_007", "judge": True}).json()

    assert started == [(SESSION, "ep_007")]
    assert detail["judge_queued"] is True


def test_labels_queue_and_calibration(client: TestClient, tmp_path: Path) -> None:
    store.append_scorecard(store.scorecards_dir(tmp_path), {
        "session_id": SESSION, "episode_id": "ep_007", "verdict": "ALERTA", "judge": True,
        "results": [{"check_id": "DES-04", "verdict": "falla", "turn": 3, "evidence": "e", "critique": "c", "source": "judge"}],
    })

    queue = client.get("/api/chats/evals/labels/queue", params={"days": 7}).json()
    assert [(i["check_id"], i["reason"]) for i in queue["items"]] == [("DES-04", "falla")]

    created = client.post("/api/chats/evals/labels", json={
        "session_id": SESSION, "episode_id": "ep_007", "check_id": "DES-04", "verdict": "falla", "note": "sí falló",
    }).json()
    assert created["ok"] is True and created["label"]["labeled_at"]

    assert client.get("/api/chats/evals/labels/queue", params={"days": 7}).json()["items"] == []
    labels = client.get("/api/chats/evals/labels", params={"session_id": SESSION, "episode_id": "ep_007"}).json()
    assert labels["labels"][0]["note"] == "sí falló"
    cal = {c["check_id"]: c for c in client.get("/api/chats/evals/calibration").json()["checks"]}
    assert (cal["DES-04"]["tp"], cal["DES-04"]["n"]) == (1, 1)


def test_invalid_inputs_are_rejected(client: TestClient) -> None:
    assert client.get("/api/chats/evals/scorecard", params={"session_id": "../etc", "episode_id": "ep_1"}).status_code == 400
    bad_label = {"session_id": SESSION, "episode_id": "ep_007", "check_id": "NOPE-1", "verdict": "falla"}
    assert client.post("/api/chats/evals/labels", json=bad_label).status_code == 400
    bad_verdict = {"session_id": SESSION, "episode_id": "ep_007", "check_id": "DES-04", "verdict": "quizás"}
    assert client.post("/api/chats/evals/labels", json=bad_verdict).status_code == 400


def test_label_stores_the_judge_verdict_it_was_made_against(client: TestClient, tmp_path: Path) -> None:
    store.append_scorecard(store.scorecards_dir(tmp_path), {
        "session_id": SESSION, "episode_id": "ep_007", "verdict": "ALERTA", "judge": True,
        "results": [{"check_id": "DES-04", "verdict": "falla", "turn": 3, "evidence": "e", "critique": "c", "source": "judge"}],
    })

    created = client.post("/api/chats/evals/labels", json={
        "session_id": SESSION, "episode_id": "ep_007", "check_id": "DES-04", "verdict": "pasa",
    }).json()

    assert created["label"]["judge_verdict"] == "falla"
    cal = {c["check_id"]: c for c in client.get("/api/chats/evals/calibration").json()["checks"]}
    assert (cal["DES-04"]["fp"], cal["DES-04"]["n"]) == (1, 1)


def test_ids_with_a_trailing_newline_are_rejected(client: TestClient) -> None:
    r = client.get("/api/chats/evals/scorecard", params={"session_id": SESSION + "\n", "episode_id": "ep_007"})
    assert r.status_code == 400


async def test_start_judge_workflow_reuses_the_run_already_in_flight(monkeypatch) -> None:
    """Doble clic en «Recalcular con juez» no debe pagar dos veces el juez."""
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from src.sdk import runtime as sdk_runtime

    class FakeClient:
        def __init__(self) -> None:
            self.ids: list[str] = []

        async def start_workflow(self, name, arg, *, id, task_queue):
            self.ids.append(id)
            if len(self.ids) > 1:
                raise WorkflowAlreadyStartedError(id, name, run_id="run-1")

    fake = FakeClient()

    async def get_client():
        return fake

    monkeypatch.setattr(sdk_runtime, "get_temporal_client", get_client)

    first = await api._start_judge_workflow(SESSION, "ep_007")
    second = await api._start_judge_workflow(SESSION, "ep_007")

    assert first == second == f"scorecard-{SESSION}-ep_007"
    assert len(fake.ids) == 2
