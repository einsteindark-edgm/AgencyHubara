"""Activities de la corrida en la CAJA del laboratorio (Temporal local).

Leen `orders/` y `bench/` del S3 del laboratorio, trabajan en `LAB_ROOT`
(/lab) y escriben SOLO en `runs/<corrida>/`. El avance va a
`runs/<corrida>/progress.json`, que el lanzador de producción sigue.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_lab.run.contracts import ProgressUpdate, PublishResult, RunPlan
from src.plugins.chats.agent.sales_lab.run.publish import publish_control
from src.sdk.labkit import LabStorePort, get_lab_store

SALES_WORKSPACE = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"


def _lab_root() -> Path:
    return Path(os.getenv("LAB_ROOT") or "/lab")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> LabStorePort:
    store = get_lab_store()
    if store is None:
        raise ApplicationError("la caja no tiene LAB_BUCKET", non_retryable=True)
    return store


def _download_bench(store: LabStorePort, bench_id: str, dest: Path) -> None:
    prefix = f"bench/{bench_id}/"
    if (dest / "manifest.json").is_file():
        return  # ya bajado en una corrida anterior de esta caja
    keys = store.list_keys(prefix)
    if f"{prefix}manifest.json" not in keys:
        raise ApplicationError(f"el banco {bench_id} no tiene manifiesto: está incompleto", non_retryable=True)
    for key in keys:
        target = dest / key[len(prefix):]
        target.parent.mkdir(parents=True, exist_ok=True)
        data = store.get_bytes(key)
        if data is not None:
            target.write_bytes(data)


@activity.defn(name="lab_run_prepare")
async def prepare_run_activity(run_id: str) -> RunPlan:
    store = _store()
    raw = await asyncio.to_thread(store.get_bytes, f"orders/{run_id}.json")
    if raw is None:
        raise ApplicationError(f"no hay orden para la corrida {run_id}", non_retryable=True)
    order = json.loads(raw)
    bench_id = str(order["bench_id"])
    await asyncio.to_thread(_download_bench, store, bench_id, _lab_root() / "bench" / bench_id)
    return RunPlan(
        run_id=run_id,
        bench_id=bench_id,
        arms=[str(a) for a in order.get("arms") or []],
        reps=int(order.get("reps") or 1),
        spend_limit_usd=float(order.get("spend_limit_usd") or 0.0),
        image=str(order.get("image") or ""),
    )


@activity.defn(name="lab_run_publish_control")
async def publish_control_activity(plan: RunPlan) -> PublishResult:
    bench_dir = _lab_root() / "bench" / plan.bench_id
    case_set = await asyncio.to_thread(build_cases, bench_dir, sales_workspace=SALES_WORKSPACE)
    local = _lab_root() / "runs" / plan.run_id
    local.mkdir(parents=True, exist_ok=True)
    (local / "cases.jsonl").write_text(
        "\n".join(json.dumps(c.to_dict(), ensure_ascii=False) for c in case_set.cases) + "\n", encoding="utf-8"
    )
    manifest = await asyncio.to_thread(
        publish_control, bench_dir, case_set, _store(), run_id=plan.run_id, order=asdict(plan)
    )
    return PublishResult(sessions=int(manifest["counts"]["sessions"]), cases=int(manifest["counts"]["cases"]))


@activity.defn(name="lab_run_progress")
async def write_progress_activity(update: ProgressUpdate) -> None:
    store = _store()
    key = f"runs/{update.run_id}/progress.json"
    raw = await asyncio.to_thread(store.get_bytes, key)
    try:
        previous = json.loads(raw) if raw else {}
    except ValueError:
        previous = {}
    now = _now_ms()
    progress = {
        **asdict(update),
        "started_at_ms": previous.get("started_at_ms") or now,
        "updated_at_ms": now,
    }
    await asyncio.to_thread(store.put_bytes, key, json.dumps(progress, ensure_ascii=False).encode())


@activity.defn(name="lab_run_cancel_requested")
async def cancel_requested_activity(run_id: str) -> bool:
    return (_lab_root() / "runs" / run_id / "CANCEL").exists()


LAB_RUN_ACTIVITIES = [
    prepare_run_activity,
    publish_control_activity,
    write_progress_activity,
    cancel_requested_activity,
]
