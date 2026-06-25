"""API del buzón de análisis de ``ads`` — el router (Cognito-protegido por el loader; acá lo
montamos solo). POST /runs dispara (crea record + launcher + poller), GET /runs/{id} el
snapshot, POST /runs/{id}/approve resume el HITL, GET /agents el catálogo del selector
(agente + JSON de ejemplo del viewer). Launcher + poller mockeados (sin AWS ni Conductor).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.ads.api.analysis as api
from src.plugins.ads.runs import record


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
    app.include_router(api.router, prefix="/api/ads/analysis")
    c = TestClient(app)
    c.fake = fake  # type: ignore[attr-defined]
    return c


def test_post_runs_dispara_y_devuelve_run_id(client) -> None:
    r = client.post(
        "/api/ads/analysis/runs",
        json={"agent": "ads-analytics", "input": {"meta_insights": {}}},
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    rec = record.read_run(run_id)
    assert rec["agent"] == "ads-analytics"
    assert rec["status"] == "running"
    assert rec["execution_id"] == "exec-9"


def test_get_run_devuelve_el_record(client) -> None:
    run_id = client.post(
        "/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {}}
    ).json()["run_id"]
    r = client.get(f"/api/ads/analysis/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["run_id"] == run_id


def test_get_run_inexistente_404(client) -> None:
    assert client.get("/api/ads/analysis/runs/nope").status_code == 404


def test_approve_resume_con_la_decision(client) -> None:
    run_id = client.post(
        "/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {}}
    ).json()["run_id"]
    r = client.post(
        f"/api/ads/analysis/runs/{run_id}/approve",
        json={"decision": {"approved": True, "by": "ed"}},
    )
    assert r.status_code == 200
    assert client.fake.resumed == ("exec-9", {"approved": True, "by": "ed"})


def test_get_agents_trae_el_catalogo_con_input_de_ejemplo(client) -> None:
    agents = client.get("/api/ads/analysis/agents").json()
    ads_analytics = next(a for a in agents if a["id"] == "ads-analytics")
    # el JSON del viewer (caso dia-del-padre): los 3 bloques del seed del supervisor.
    assert set(ads_analytics["example_input"]) == {
        "meta_insights",
        "manual_sales",
        "entities_payload",
    }
