"""API del buzón de análisis de `ads` — el router (Cognito-protegido por el loader; acá lo
montamos solo). POST /runs crea el record `pending` + dispara el launch en BACKGROUND (mockeado
acá — no esperamos a la caja), GET /runs/{id} el snapshot, POST /runs/{id}/approve resume el HITL,
GET /agents el catálogo del selector. Launcher + launch mockeados (sin AWS ni Conductor).
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

    def fetch_status(self, execution_id: str) -> dict:
        return {"status": "COMPLETED", "tasks": []}


@pytest.fixture()
def client(monkeypatch):
    fake = _FakeLauncher()
    launched: list = []
    monkeypatch.setattr(api, "_get_launcher", lambda: fake)
    # El launch (despertar caja + despachar + pollear) corre en background — lo capturamos sin correrlo.
    monkeypatch.setattr(
        api, "_spawn_launch", lambda run_id, agent, input: launched.append((run_id, agent, input))
    )
    resumed: list = []
    monkeypatch.setattr(
        api,
        "_spawn_resume",
        lambda run_id, execution_id, decision: resumed.append((run_id, execution_id, decision)),
    )
    app = FastAPI()
    app.include_router(api.router, prefix="/api/ads/analysis")
    c = TestClient(app)
    c.fake = fake  # type: ignore[attr-defined]
    c.launched = launched  # type: ignore[attr-defined]
    c.resumed = resumed  # type: ignore[attr-defined]
    return c


def test_post_runs_crea_pending_y_dispara_el_launch(client) -> None:
    r = client.post(
        "/api/ads/analysis/runs",
        json={"agent": "ads-analytics", "input": {"meta_insights": {}}},
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    rec = record.read_run(run_id)
    assert rec["agent"] == "ads-analytics"
    # El endpoint NO espera a la caja (que tarda minutos en cold start); nace `pending` y el
    # launch corre aparte. El progreso llega por el SSE.
    assert rec["status"] == "pending"
    assert client.launched == [(run_id, "ads-analytics", {"meta_insights": {}})]


def test_post_runs_rechaza_agente_desconocido(client) -> None:
    # `agent` se interpola en el comando SSM de la caja → SOLO agentes del catálogo (defensa inyección).
    r = client.post("/api/ads/analysis/runs", json={"agent": "ads-analytics; rm -rf /", "input": {}})
    assert r.status_code == 400
    assert client.launched == []  # ni record ni launch


def test_get_run_devuelve_el_record(client) -> None:
    run_id = client.post(
        "/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {}}
    ).json()["run_id"]
    r = client.get(f"/api/ads/analysis/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["run_id"] == run_id


def test_get_run_inexistente_404(client) -> None:
    assert client.get("/api/ads/analysis/runs/nope").status_code == 404


def test_approve_dispara_el_resume_en_background(client) -> None:
    run_id = client.post(
        "/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {}}
    ).json()["run_id"]
    # Simulamos que el launch ya despachó (run.started con execution_id) — recién ahí hay HITL que resumir.
    record.append_event(
        run_id,
        {"event_id": f"{run_id}:started", "type": "run.started", "payload": {"execution_id": "exec-9"}},
    )
    r = client.post(
        f"/api/ads/analysis/runs/{run_id}/approve",
        json={"decision": {"approved": True, "by": "ed"}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "resuming"  # el request NO espera a la caja (resume en background)
    assert client.resumed == [(run_id, "exec-9", {"approved": True, "by": "ed"})]


def test_approve_409_si_el_run_no_fue_despachado(client) -> None:
    # Record `pending` (la caja aún despierta) → no hay execution_id → 409 (no un 500 por KeyError).
    run_id = client.post(
        "/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {}}
    ).json()["run_id"]
    r = client.post(
        f"/api/ads/analysis/runs/{run_id}/approve", json={"decision": {"approved": True, "by": "ed"}}
    )
    assert r.status_code == 409


def test_get_agents_trae_el_catalogo_con_input_de_ejemplo(client) -> None:
    agents = client.get("/api/ads/analysis/agents").json()
    ads_analytics = next(a for a in agents if a["id"] == "ads-analytics")
    # el JSON del viewer (caso dia-del-padre): los 3 bloques del seed del supervisor.
    assert set(ads_analytics["example_input"]) == {
        "meta_insights",
        "manual_sales",
        "entities_payload",
    }


def test_list_runs_endpoint_devuelve_historial_versionado(client):
    """GET /api/ads/analysis/runs — el historial del inspector: cada análisis
    con fecha, estado, snapshot de entrada y resultado, más nuevo primero."""
    c = client
    r1 = c.post("/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {"v": 1}})
    r2 = c.post("/api/ads/analysis/runs", json={"agent": "ads-analytics", "input": {"v": 2}})
    rid1, rid2 = r1.json()["run_id"], r2.json()["run_id"]
    # fechas deterministas para el orden (create_run estampa el reloj real)
    for rid, ts in ((rid1, 1000), (rid2, 2000)):
        rec = record.read_run(rid)
        rec["created_at_ms"] = ts
        record._write(rec)

    body = c.get("/api/ads/analysis/runs").json()
    runs = body["runs"]
    assert [r["run_id"] for r in runs] == [rid2, rid1]
    assert runs[0]["created_at_ms"] == 2000
    assert runs[0]["input"] == {"v": 2}
    assert "events" not in runs[0]  # el log en vivo no viaja en el historial
