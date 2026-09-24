"""La corrida en la caja de punta a punta (plan PR 8): una orden del lanzador
arma los casos y los publica en `runs/`. Y el worker no arranca con llaves de
producción (proceso aparte, como en la caja)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_lab.run import activities as run_acts
from src.plugins.chats.agent.sales_lab.run.contracts import LAB_TASK_QUEUE, LabRunInput
from src.plugins.chats.agent.sales_lab.run.workflow import SIMULATION_PENDING_NOTE, LabRunWorkflow
from src.sdk.labkit import FilesystemLabStore
from tests.plugins.chats.lab.test_lab_cases import SID, _bench

RUN = "run-20260923-a1b2"
HUB = Path(__file__).resolve().parents[4]


@pytest.fixture
def box(tmp_path: Path, monkeypatch) -> dict:
    bench = _bench(tmp_path / "src")
    store = FilesystemLabStore(tmp_path / "bucket")
    for f in bench.rglob("*"):
        if f.is_file():
            store.put_file(f"bench/bench-x/{f.relative_to(bench).as_posix()}", f)
    store.put_bytes(f"orders/{RUN}.json", json.dumps({
        "run_id": RUN, "bench_id": "bench-x", "arms": ["A1", "B"], "reps": 1, "image": "ghcr.io/o/a:b",
        "estimate_usd": 20.0, "spend_limit_usd": 120.0, "requested_at_ms": 1,
    }).encode())
    root = tmp_path / "lab"
    smoke: dict = {"calls": [], "result": {"error": None, "trace": {"sent_texts": ["¡Hola!"]}}}

    async def fake_case(case, *, bench_dir, sandbox_dir, timeout_s):
        smoke["calls"].append(case["case_id"])
        return {"case_id": case["case_id"], **smoke["result"]}

    monkeypatch.setattr(run_acts, "run_case_in_subprocess", fake_case)
    monkeypatch.setattr(run_acts, "get_lab_store", lambda: store)
    monkeypatch.setenv("LAB_ROOT", str(root))
    return {"store": store, "root": root, "smoke": smoke}


async def _run(box: dict) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=LAB_TASK_QUEUE, workflows=[LabRunWorkflow], activities=run_acts.LAB_RUN_ACTIVITIES):
            return await env.client.execute_workflow(
                LabRunWorkflow.run, LabRunInput(run_id=RUN), id=f"lab-run-{RUN}", task_queue=LAB_TASK_QUEUE
            )


@pytest.mark.asyncio
async def test_an_order_builds_the_cases_and_publishes_the_control(box) -> None:
    result = await _run(box)

    assert result["phase"] == "done" and result["cases"] == 2
    store = box["store"]
    progress = json.loads(store.get_bytes(f"runs/{RUN}/progress.json"))
    assert (progress["phase"], progress["turns_done"], progress["turns_total"]) == ("done", 2, 2)
    assert progress["notes"] == [SIMULATION_PENDING_NOTE]
    assert store.get_bytes(f"runs/{RUN}/threads/{SID}.json") is not None
    assert (box["root"] / "bench" / "bench-x" / "manifest.json").is_file()


@pytest.mark.asyncio
async def test_a_smoke_turn_of_the_bench_runs_before_simulating(box) -> None:
    """Plan §3.3: antes de cada corrida, un turno de humo del banco tiene que
    pasar; si no pasa, la corrida no arranca (y dice por qué)."""
    await _run(box)

    assert box["smoke"]["calls"] == [f"{SID}/ep_001/t1"]


@pytest.mark.asyncio
async def test_a_failed_smoke_turn_fails_the_run_with_the_reason(box) -> None:
    box["smoke"]["result"] = {"error": "el turno no terminó en 600 s", "trace": None}

    result = await _run(box)

    assert result["phase"] == "failed"
    progress = json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))
    assert progress["phase"] == "failed"
    assert "turno de humo" in progress["error"] and "no terminó" in progress["error"]


@pytest.mark.asyncio
async def test_a_control_only_run_needs_no_smoke_turn(box) -> None:
    order = json.loads(box["store"].get_bytes(f"orders/{RUN}.json"))
    box["store"].put_bytes(f"orders/{RUN}.json", json.dumps({**order, "arms": ["A0"]}).encode())

    result = await _run(box)

    assert result["phase"] == "done" and box["smoke"]["calls"] == []


@pytest.mark.asyncio
async def test_a_cancel_mark_before_running_ends_the_run_cancelled(box) -> None:
    (box["root"] / "runs" / RUN).mkdir(parents=True)
    (box["root"] / "runs" / RUN / "CANCEL").touch()

    result = await _run(box)

    assert result["phase"] == "cancelled"
    assert json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_missing_order_fails_the_run_with_the_reason(box) -> None:
    (Path(box["store"].root) / "orders" / f"{RUN}.json").unlink()

    result = await _run(box)

    assert result["phase"] == "failed" and "no hay orden" in result["error"]
    assert json.loads(box["store"].get_bytes(f"runs/{RUN}/progress.json"))["phase"] == "failed"


def _worker(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEDUSA_", "WHATSAPP_", "META_", "TEMPORAL_"))}
    env.update({"TEMPORAL_URL": "temporal:7233", "TEMPORAL_NAMESPACE": "hubara-lab", "HUBARA_ENV": "lab",
                "LAB_ROOT": "/lab", "ENABLED_PLUGINS": "chats", **env_extra})
    return subprocess.run([sys.executable, "-m", "src.plugins.chats.workers.sales_lab"], cwd=HUB, env=env,
                          capture_output=True, text=True, timeout=120)


def test_worker_refuses_to_start_with_a_production_key() -> None:
    out = _worker({"WHATSAPP_ACCESS_TOKEN": "EAAG-real", "LAB_RUN_ID": RUN})

    assert out.returncode == 2
    assert "WHATSAPP_ACCESS_TOKEN" in out.stderr


def test_worker_refuses_to_start_against_temporal_cloud() -> None:
    out = _worker({"TEMPORAL_URL": "us-west-2.aws.api.temporal.io:7233", "LAB_RUN_ID": RUN})

    assert out.returncode == 2
    assert "TEMPORAL_URL" in out.stderr
