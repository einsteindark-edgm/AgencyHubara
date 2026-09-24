"""Contrato `lab@v1`, lado lanzador: el botón "Nueva corrida" (plan §3.7 y §4.3).

Rutas bajo `/api/chats/lab` (protegidas por `require_auth` como toda ruta de
plugin). Las de lectura de corridas (banco, conversaciones, hilo, resumen)
llegan con el PR 8.

  GET  /lab/estimate?arms=A1,B,C&reps=3&bench=new
  POST /lab/runs                  {arms, reps, bench}  → 202 | 409 | 422 | 503
  GET  /lab/runs/active
  POST /lab/runs/active/cancel

Candados: A1 (el control) siempre va; repeticiones 1 o 3; una corrida a la
vez (workflow `lab-launch` con conflicto FAIL → 409); topes por corrida y por
mes desde Terraform (`LAB_MAX_USD_PER_RUN` / `LAB_MAX_USD_PER_MONTH`) → 422.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from src.plugins.chats.agent.sales_lab.launch.bench_export import plan_bench_export
from src.plugins.chats.agent.sales_lab.launch.contracts import LabLaunchInput
from src.plugins.chats.agent.sales_lab.launch.costs import check_caps, estimate_run_usd, month_spent_usd
from src.plugins.chats.agent.sales_eval.workflows.lab_launch import LAB_LAUNCH_WORKFLOW_ID
from src.sdk import get_task_queue
from src.sdk.labkit import IMAGE_RE, LabStorePort, get_lab_store
from src.sdk.runtime import WORKSPACE_VAULT_DIR, get_temporal_client

router = APIRouter()

ARMS: dict[str, str] = {
    "A1": "Bot actual (control)",
    "B": "Bot nuevo + Jev (OpenRouter)",
    "C": "Bot nuevo + OpenAI (OpenRouter)",
}
_REPS = (1, 3)
_BENCH_RE = re.compile(r"^bench-[a-z0-9-]{6,64}$")
_BOGOTA = timezone(timedelta(hours=-5))
_DEFAULT_SINCE = "2026-09-10"


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _temporal_client() -> Any:
    return await get_temporal_client()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _since_ms() -> int:
    raw = (os.getenv("LAB_BENCH_SINCE") or _DEFAULT_SINCE).strip()
    day = date.fromisoformat(raw)
    return int(datetime(day.year, day.month, day.day, tzinfo=_BOGOTA).timestamp() * 1000)


def _store() -> LabStorePort:
    store = get_lab_store()
    if store is None:
        raise HTTPException(503, detail="El laboratorio no está configurado (falta LAB_BUCKET).")
    return store


def _parse_arms(arms: list[str]) -> list[str]:
    wanted = [a.strip() for a in arms if a and a.strip()]
    if "A1" not in wanted or any(a not in ARMS for a in wanted) or len(set(wanted)) != len(wanted):
        raise HTTPException(422, detail={"reason": "arms", "message": "Bots válidos: A1 (siempre), B y C."})
    return [a for a in ARMS if a in wanted]


def _parse_reps(reps: int) -> int:
    if reps not in _REPS:
        raise HTTPException(422, detail={"reason": "reps", "message": "Repeticiones: 1 o 3."})
    return reps


def _bench_turns(store: LabStorePort, bench: str) -> tuple[str | None, int]:
    """(bench_id o None si es nuevo, turnos del cliente del banco)."""
    if bench in ("", "new"):
        vault = _vault_dir()
        plan = plan_bench_export(
            vault,
            state_dir=None,
            catalog_dir=None,
            bench_id="estimate",
            since_ms=_since_ms(),
            now_ms=_now_ms(),
            internal_numbers=[n for n in (os.getenv("LAB_INTERNAL_NUMBERS") or "").split(",") if n.strip()],
        )
        return None, plan.customer_turns
    if not _BENCH_RE.fullmatch(bench):
        raise HTTPException(422, detail={"reason": "bench", "message": "Banco inválido."})
    raw = store.get_bytes(f"bench/{bench}/manifest.json")
    if raw is None:
        raise HTTPException(422, detail={"reason": "bench", "message": f"El banco {bench} no existe o está incompleto."})
    counts = (json.loads(raw).get("counts") or {}) if raw else {}
    return bench, int(counts.get("customer_turns") or 0)


def _estimate(store: LabStorePort, arms: list[str], reps: int, bench: str) -> dict[str, Any]:
    bench_id, turns = _bench_turns(store, bench)
    estimate = estimate_run_usd(arms, reps=reps, turns=turns)
    run_cap = _env_float("LAB_MAX_USD_PER_RUN", 120.0)
    month_cap = _env_float("LAB_MAX_USD_PER_MONTH", 300.0)
    spent = month_spent_usd(store, now_ms=_now_ms())
    caps = check_caps(estimate, run_cap=run_cap, month_cap=month_cap, month_spent=spent)
    return {
        "bench_id": bench_id,
        "turns": turns,
        "arms": [{"id": a, "label": ARMS[a], "selected": a in arms} for a in ARMS],
        "reps": reps,
        "estimate_usd": estimate,
        "run_cap_usd": run_cap,
        "month_cap_usd": month_cap,
        "month_spent_usd": spent,
        "month_left_usd": caps.month_left,
        "fits": caps.fits,
        "reason": caps.reason,
        "spend_limit_usd": round(min(run_cap, caps.month_left), 2),
    }


@router.get("/lab/estimate")
def estimate(
    arms: str = Query("A1", max_length=20),
    reps: int = Query(1),
    bench: str = Query("new", max_length=80),
) -> dict[str, Any]:
    store = _store()
    return _estimate(store, _parse_arms(arms.split(",")), _parse_reps(reps), bench)


async def _active_status(client: Any) -> dict[str, Any] | None:
    from temporalio.client import WorkflowExecutionStatus

    handle = client.get_workflow_handle(LAB_LAUNCH_WORKFLOW_ID)
    try:
        desc = await handle.describe()
    except Exception:  # noqa: BLE001 — nunca se lanzó una corrida
        return None
    if desc.status != WorkflowExecutionStatus.RUNNING:
        return None
    status = dict(await handle.query("status"))
    run_id = status.get("run_id")
    store = get_lab_store()
    if run_id and store is not None:
        raw = await asyncio.to_thread(store.get_bytes, f"runs/{run_id}/progress.json")
        try:
            progress = json.loads(raw) if raw else {}
        except ValueError:
            progress = {}
        for key in ("turns_done", "turns_total", "spent_usd"):
            if isinstance(progress, dict) and key in progress:
                status[key] = progress[key]
        if isinstance(progress, dict) and progress.get("phase") and status.get("phase") == "running":
            status["box_phase"] = progress["phase"]
    return status


@router.post("/lab/runs", status_code=202)
async def launch(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    from temporalio.common import WorkflowIDConflictPolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    store = _store()
    raw_arms = body.get("arms") if isinstance(body.get("arms"), list) else []
    arms = _parse_arms([str(a) for a in raw_arms])
    reps = _parse_reps(body.get("reps") if isinstance(body.get("reps"), int) else 0)
    bench = str(body.get("bench") or "new")
    image = (os.getenv("HUBARA_IMAGE") or "").strip()
    if not IMAGE_RE.fullmatch(image):
        raise HTTPException(503, detail="No conozco la imagen desplegada (HUBARA_IMAGE).")
    # Recorre el vault y lista S3: fuera del event loop, que también atiende el
    # webhook de WhatsApp, la bandeja y el SSE.
    est = await asyncio.to_thread(_estimate, store, arms, reps, bench)
    if not est["fits"]:
        raise HTTPException(422, detail={"reason": est["reason"], "estimate": est})
    now = datetime.fromtimestamp(_now_ms() / 1000, tz=_BOGOTA)
    run_id = f"run-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    client = await _temporal_client()
    try:
        await client.start_workflow(
            "LabLaunchWorkflow",
            LabLaunchInput(
                run_id=run_id,
                arms=arms,
                reps=reps,
                bench_id=est["bench_id"],
                image=image,
                since_ms=_since_ms(),
                estimate_usd=est["estimate_usd"],
                spend_limit_usd=est["spend_limit_usd"],
            ),
            id=LAB_LAUNCH_WORKFLOW_ID,
            task_queue=get_task_queue("chats", "sales_eval"),
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
        )
    except WorkflowAlreadyStartedError:
        raise HTTPException(409, detail={"message": "Ya hay una corrida en curso.", "active": await _active_status(client)})
    return {"run_id": run_id, "workflow_id": LAB_LAUNCH_WORKFLOW_ID, "estimate": est}


@router.get("/lab/runs/active")
async def active() -> dict[str, Any]:
    return {"active": await _active_status(await _temporal_client())}


@router.post("/lab/runs/active/cancel", status_code=202)
async def cancel_active() -> dict[str, Any]:
    client = await _temporal_client()
    status = await _active_status(client)
    if status is None:
        raise HTTPException(404, detail="No hay una corrida en curso.")
    await client.get_workflow_handle(LAB_LAUNCH_WORKFLOW_ID).signal("cancel")
    return {"cancel_requested": True, "run_id": status.get("run_id")}
