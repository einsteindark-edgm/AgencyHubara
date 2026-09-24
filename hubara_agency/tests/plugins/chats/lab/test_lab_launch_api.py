"""API del botón "Nueva corrida" (plan §3.7 y §4.3) bajo /api/chats/lab.

* `GET /estimate`: costo estimado, topes y lo que queda del mes.
* `POST /runs`: lanza la corrida; 409 si ya hay una en curso, 422 si no cabe
  en el tope o si los datos no tienen la forma pedida (A1 siempre va).
* `GET /runs/active`: fase, turnos hechos sobre el total, gasto y error.
* `POST /runs/active/cancel`: pide cancelar la corrida en curso.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from src.plugins.chats.api import lab as api
from src.sdk.labkit import FilesystemLabStore
from tests.plugins.chats.lab.test_bench_export import NOW_MS, _vault


class _Desc:
    def __init__(self, status) -> None:
        self.status = status


class FakeHandle:
    def __init__(self, client: "FakeClient") -> None:
        self.client = client

    async def describe(self):
        if self.client.running is None:
            from temporalio.service import RPCError

            raise RPCError("not found", 5, b"")
        return _Desc(WorkflowExecutionStatus.RUNNING if self.client.running else WorkflowExecutionStatus.COMPLETED)

    async def query(self, name, *args, **kw):
        return dict(self.client.status)

    async def signal(self, name, *args, **kw):
        self.client.signals.append(name if isinstance(name, str) else name.__name__)


class FakeClient:
    def __init__(self) -> None:
        self.running: bool | None = None
        self.started: list[dict] = []
        self.signals: list[str] = []
        self.status: dict = {}

    async def start_workflow(self, workflow, arg, *, id, task_queue, id_conflict_policy=None, **kw):
        if self.running:
            raise WorkflowAlreadyStartedError(id, "LabLaunchWorkflow")
        self.started.append({"workflow": workflow, "input": arg, "id": id, "task_queue": task_queue,
                             "policy": id_conflict_policy})
        self.running = True
        self.status = {"phase": "exporting", "run_id": arg.run_id, "bench_id": arg.bench_id}
        return FakeHandle(self)

    def get_workflow_handle(self, workflow_id):
        return FakeHandle(self)


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> dict:
    vault = _vault(tmp_path)
    store = FilesystemLabStore(tmp_path / "bucket")
    client = FakeClient()

    async def _client():
        return client

    monkeypatch.setattr(api, "get_lab_store", lambda: store)
    monkeypatch.setattr(api, "_vault_dir", lambda: vault)
    monkeypatch.setattr(api, "_temporal_client", _client)
    monkeypatch.setattr(api, "_now_ms", lambda: NOW_MS)
    monkeypatch.setenv("HUBARA_IMAGE", "ghcr.io/einsteindark-edgm/agencyhubara:abc123")
    monkeypatch.setenv("LAB_MAX_USD_PER_RUN", "120")
    monkeypatch.setenv("LAB_MAX_USD_PER_MONTH", "300")
    monkeypatch.setenv("LAB_BENCH_SINCE", "2026-09-09")
    monkeypatch.setenv("LAB_INTERNAL_NUMBERS", "573000000099")
    app = FastAPI()
    app.include_router(api.router, prefix="/api/chats")
    return {"http": TestClient(app), "client": client, "store": store}


def test_estimate_for_a_new_bench(env) -> None:
    data = env["http"].get("/api/chats/lab/estimate", params={"arms": "A1,B,C", "reps": 3, "bench": "new"}).json()

    assert data["turns"] == 1
    assert data["estimate_usd"] > 0
    assert (data["run_cap_usd"], data["month_cap_usd"], data["month_left_usd"]) == (120.0, 300.0, 300.0)
    assert data["fits"] is True
    assert [a["id"] for a in data["arms"]] == ["A1", "B", "C"]


def test_launch_starts_the_single_run_workflow(env) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B"], "reps": 1, "bench": "new"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["run_id"].startswith("run-")
    [started] = env["client"].started
    assert started["id"] == "lab-launch"
    assert started["workflow"] == "LabLaunchWorkflow"
    assert started["task_queue"] == "queue-sales-eval"
    assert started["policy"] == WorkflowIDConflictPolicy.FAIL
    inp = started["input"]
    assert (inp.arms, inp.reps, inp.bench_id, inp.image) == (
        ["A1", "B"], 1, None, "ghcr.io/einsteindark-edgm/agencyhubara:abc123"
    )
    assert inp.spend_limit_usd == 120.0


def test_second_launch_is_a_409_with_the_active_run(env) -> None:
    env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})

    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["active"]["phase"] == "exporting"


def test_launch_that_does_not_fit_the_month_is_a_422(env) -> None:
    env["store"].put_bytes("runs/run-old/progress.json", json.dumps({"started_at_ms": NOW_MS - 1000, "spent_usd": 299.9}).encode())

    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B", "C"], "reps": 3, "bench": "new"})

    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "month_cap"
    assert env["client"].started == []


@pytest.mark.parametrize(
    "body",
    [
        {"arms": ["B"], "reps": 1, "bench": "new"},  # sin el control A1
        {"arms": ["A1", "Z"], "reps": 1, "bench": "new"},
        {"arms": ["A1"], "reps": 2, "bench": "new"},
        {"arms": ["A1"], "reps": 1, "bench": "../x"},
    ],
)
def test_malformed_launch_is_a_422(env, body: dict) -> None:
    assert env["http"].post("/api/chats/lab/runs", json=body).status_code == 422


def test_reusing_a_missing_bench_is_a_422(env) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "bench-run-20260901-dead"})

    assert resp.status_code == 422


def test_active_run_merges_the_workflow_phase_and_the_box_progress(env) -> None:
    env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})
    run_id = env["client"].status["run_id"]
    env["client"].status["phase"] = "running"
    env["store"].put_bytes(
        f"runs/{run_id}/progress.json",
        json.dumps({"phase": "running", "turns_done": 12, "turns_total": 40, "spent_usd": 2.25}).encode(),
    )

    data = env["http"].get("/api/chats/lab/runs/active").json()

    assert data["active"]["phase"] == "running"
    assert (data["active"]["turns_done"], data["active"]["turns_total"], data["active"]["spent_usd"]) == (12, 40, 2.25)


def test_no_active_run(env) -> None:
    assert env["http"].get("/api/chats/lab/runs/active").json() == {"active": None}


def test_cancel_signals_the_workflow_or_404(env) -> None:
    assert env["http"].post("/api/chats/lab/runs/active/cancel").status_code == 404
    env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})

    resp = env["http"].post("/api/chats/lab/runs/active/cancel")

    assert resp.status_code == 202
    assert env["client"].signals == ["cancel"]


def test_lab_without_store_is_a_503(env, monkeypatch) -> None:
    monkeypatch.setattr(api, "get_lab_store", lambda: None)

    assert env["http"].get("/api/chats/lab/estimate", params={"arms": "A1", "reps": 1}).status_code == 503


def _off_the_event_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def test_launch_estimates_off_the_event_loop(env, monkeypatch) -> None:
    """La API corre en UN proceso que también atiende el webhook de WhatsApp,
    la bandeja y el SSE: recorrer el vault y listar S3 dentro del event loop
    los congela mientras dura."""
    seen: list[bool] = []
    real = api._estimate

    def spy(*args, **kwargs):
        seen.append(_off_the_event_loop())
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "_estimate", spy)

    env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})

    assert seen == [True]


def test_the_active_run_reads_s3_off_the_event_loop(env, monkeypatch) -> None:
    env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "new"})
    env["client"].status = {"phase": "running", "run_id": "run-20260923-a1b2"}
    seen: list[bool] = []
    real_get = env["store"].get_bytes

    def spy(key):
        seen.append(_off_the_event_loop())
        return real_get(key)

    monkeypatch.setattr(env["store"], "get_bytes", spy)

    env["http"].get("/api/chats/lab/runs/active")

    assert seen and all(seen)


def test_a_bench_id_must_match_whole(env) -> None:
    resp = env["http"].get("/api/chats/lab/estimate", params={"bench": "bench-run-20260920-ffff\n"})

    assert resp.status_code == 422 and resp.json()["detail"]["message"] == "Banco inválido."
