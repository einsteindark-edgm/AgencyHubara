"""API del buzón graphagents — el router (Cognito-protegido por el loader; acá lo montamos
solo). POST /runs dispara (crea record + launcher + poller), GET /runs/{id} el snapshot,
POST /runs/{id}/approve resume el HITL, GET /agents el catálogo del selector (agente + JSON
de ejemplo del viewer). Launcher + poller mockeados (sin AWS ni Conductor).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.graphagents.api as api
from src.plugins.graphagents.runs import record


class _FakeLauncher:
    def __init__(self) -> None:
        self.resumed = None

    def start_box(self) -> None:
        pass

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:
        return "exec-9"

    def resume(self, execution_id: str, decision: dict) -> None:
        self.resumed = (execution_id, decision)


@pytest.fixture()
def client(monkeypatch):
    fake = _FakeLauncher()
    monkeypatch.setattr(api, "_get_launcher", lambda: fake)
    monkeypatch.setattr(api, "_spawn_poller", lambda run_id, execution_id: None)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/graphagents")
    c = TestClient(app)
    c.fake = fake  # type: ignore[attr-defined]
    return c


def test_post_runs_dispara_y_devuelve_run_id(client) -> None:
    r = client.post("/api/graphagents/runs", json={"agent": "greeter", "input": {"name": "ada"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    rec = record.read_run(run_id)
    assert rec["agent"] == "greeter"
    assert rec["status"] == "running"
    assert rec["execution_id"] == "exec-9"


def test_get_run_devuelve_el_record(client) -> None:
    run_id = client.post("/api/graphagents/runs", json={"agent": "greeter", "input": {}}).json()["run_id"]
    r = client.get(f"/api/graphagents/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["run_id"] == run_id


def test_get_run_inexistente_404(client) -> None:
    assert client.get("/api/graphagents/runs/nope").status_code == 404


def test_approve_resume_con_la_decision(client) -> None:
    run_id = client.post("/api/graphagents/runs", json={"agent": "g", "input": {}}).json()["run_id"]
    r = client.post(
        f"/api/graphagents/runs/{run_id}/approve", json={"decision": {"approved": True, "by": "ed"}}
    )
    assert r.status_code == 200
    assert client.fake.resumed == ("exec-9", {"approved": True, "by": "ed"})


def test_get_agents_trae_el_catalogo_con_input_de_ejemplo(client) -> None:
    agents = client.get("/api/graphagents/agents").json()
    greeter = next(a for a in agents if a["id"] == "greeter")
    assert greeter["example_input"] == {"name": "ada"}  # el JSON del viewer
