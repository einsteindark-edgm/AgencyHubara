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
    data = env["http"].get("/api/chats/lab/estimate", params={"arms": "A1,B", "reps": 3, "bench": "new"}).json()

    assert data["turns"] == 1
    assert data["estimate_usd"] > 0
    assert (data["run_cap_usd"], data["month_cap_usd"], data["month_left_usd"]) == (120.0, 300.0, 300.0)
    assert data["fits"] is True
    # Motor de decisiones F4: B0 (workflow V2 con reglas) separa el efecto del
    # workflow nuevo del efecto de Jev (B = V2 con Jev).
    assert [a["id"] for a in data["arms"]] == ["A1", "B0", "B"]
    assert [a["selected"] for a in data["arms"]] == [True, False, True]


def test_launch_with_the_new_workflow_without_jev(env) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["B0", "A1", "B"], "reps": 1, "bench": "new"})

    assert resp.status_code == 202, resp.text
    [started] = env["client"].started
    assert started["input"].arms == ["A1", "B0", "B"]


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

    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B"], "reps": 3, "bench": "new"})

    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "month_cap"
    assert env["client"].started == []


@pytest.mark.parametrize(
    "body",
    [
        {"arms": ["B"], "reps": 1, "bench": "new"},  # sin el control A1
        {"arms": ["A1", "Z"], "reps": 1, "bench": "new"},
        {"arms": ["A1", "C"], "reps": 1, "bench": "new"},  # OpenAI se quitó (2026-09-28)
        {"arms": ["A1"], "reps": 2, "bench": "new"},
        {"arms": ["A1"], "reps": 1, "bench": "../x"},
    ],
)
def test_malformed_launch_is_a_422(env, body: dict) -> None:
    assert env["http"].post("/api/chats/lab/runs", json=body).status_code == 422


def test_reusing_a_missing_bench_is_a_422(env) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": "bench-run-20260901-dead"})

    assert resp.status_code == 422


def test_a_hand_built_bench_can_be_repeated(env) -> None:
    """Los casos armados a mano (`caso-4148-real`, contrafactuales) no se llaman
    `bench-…`: también se repiten desde «Nueva corrida»."""
    env["store"].put_bytes("bench/caso-4148-real/manifest.json", json.dumps({"counts": {"customer_turns": 3}}).encode())

    est = env["http"].get("/api/chats/lab/estimate", params={"arms": "A1,B", "reps": 1, "bench": "caso-4148-real"})
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B"], "reps": 1, "bench": "caso-4148-real"})

    assert est.status_code == 200, est.text
    assert (est.json()["bench_id"], est.json()["turns"]) == ("caso-4148-real", 3)
    assert resp.status_code == 202, resp.text
    assert env["client"].started[0]["input"].bench_id == "caso-4148-real"


@pytest.mark.parametrize("bench", ["../x", "caso/4148", "Caso-4148", "caso.4148", "abc"])
def test_a_bench_name_is_still_a_safe_path_segment(env, bench: str) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1"], "reps": 1, "bench": bench})

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
    assert env["http"].get("/api/chats/lab/runs/active").json() == {"active": None, "last": None}


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


def test_the_last_run_that_failed_before_reporting_stays_visible(env) -> None:
    """Si la caja no prende o nunca reporta, la corrida no tiene progreso en
    S3: al cerrar el lanzador, su error sigue a la vista (antes desaparecía y el
    operador relanzaba a ciegas)."""
    env["client"].running = False
    env["client"].status = {"phase": "failed", "run_id": "run-20260923-a1b2", "error": "la caja no prendió"}

    body = env["http"].get("/api/chats/lab/runs/active").json()

    assert body["active"] is None
    assert (body["last"]["phase"], body["last"]["error"]) == ("failed", "la caja no prendió")


# ── Un brazo con paquete de decisión: `B@<paquete>` (PAQUETES_DE_DECISION.md F6) ──


def test_the_estimate_lists_the_decision_bundles(env, monkeypatch) -> None:
    """El lanzador ofrece los paquetes del repo (los que trae la imagen) para
    correr el bot nuevo con uno distinto al de la tienda."""
    monkeypatch.delenv("SALES_DECISIONS_BUNDLE", raising=False)
    data = env["http"].get("/api/chats/lab/estimate", params={"arms": "A1,B", "reps": 1, "bench": "new"}).json()

    from src.plugins.chats.shared.store_pack import BUNDLES_DIR

    # ventas-2 viaja en la imagen de esta tienda (no en un clon de forge).
    others = [{"id": "ventas-2", "version": 2, "active": False}] if (BUNDLES_DIR / "ventas-2").is_dir() else []
    assert data["bundles"] == [{"id": "ventas", "version": 1, "active": True}, *others]
    assert data["bundle_arms"] == ["B0", "B"]


def test_launch_with_an_arm_that_pins_a_bundle(env, monkeypatch) -> None:
    """La tienda corre `ventas-2` (Terraform): `B@ventas` compara contra el anterior."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-2")
    body = {"arms": ["B@ventas", "A1", "B"], "reps": 1, "bench": "new"}
    resp = env["http"].post("/api/chats/lab/runs", json=body)

    assert resp.status_code == 202, resp.text
    [started] = env["client"].started
    assert started["input"].arms == ["A1", "B", "B@ventas"]


def test_the_launch_carries_the_store_bundle(env, monkeypatch) -> None:
    """La caja no lee la config de la tienda: el lanzador le manda el paquete
    activo (premortem 2026-10-02: si no, «B» corría el default del código)."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-2")
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B", "B@ventas"], "reps": 1, "bench": "new"})

    assert resp.status_code == 202, resp.text
    [started] = env["client"].started
    assert started["input"].store_bundle == "ventas-2"


def test_pinning_the_store_bundle_is_the_same_bot_and_is_refused(env, monkeypatch) -> None:
    """`B@<el de la tienda>` es B otra vez: un brazo entero gastado en ruido."""
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-2")
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": ["A1", "B", "B@ventas-2"], "reps": 1, "bench": "new"})

    assert resp.status_code == 422
    assert "ventas-2" in json.dumps(resp.json(), ensure_ascii=False)


@pytest.mark.parametrize(
    "arms",
    [
        ["A1", "B@no-existe"],      # el paquete no existe en la imagen
        ["A1", "A1@ventas"],        # el bot de hoy no le pregunta a Jev: no fija paquete
        ["A1", "B@ventas", "B@ventas"],
        ["A1", "B@../ventas"],
    ],
)
def test_a_bundle_arm_that_cannot_run_is_a_422(env, arms: list[str]) -> None:
    resp = env["http"].post("/api/chats/lab/runs", json={"arms": arms, "reps": 1, "bench": "new"})

    assert resp.status_code == 422


def test_the_results_of_a_bundle_arm_can_be_read() -> None:
    """Las lecturas (traza, evaluaciones, resumen) aceptan el brazo con su
    paquete; la forma sigue siendo un segmento de ruta seguro."""
    from fastapi import HTTPException

    assert api._arm("B@ventas-2") == "B@ventas-2"
    for bad in ("B@../x", "Z@ventas", "B@"):
        with pytest.raises(HTTPException):
            api._arm(bad)
