"""Contrato `lab@v1`, lado lanzador: el botón "Nueva corrida" (plan §3.7 y §4.3).

Rutas bajo `/api/chats/lab` (protegidas por `require_auth` como toda ruta de
plugin).

Lanzador (PR 7):
  GET  /lab/estimate?arms=A1,B,C&reps=3&bench=new
  POST /lab/runs                  {arms, reps, bench}  → 202 | 409 | 422 | 503
  GET  /lab/runs/active
  POST /lab/runs/active/cancel

Lecturas (PR 8), SOLO desde `runs/<corrida>/` del S3 del laboratorio:
  GET  /lab/runs
  GET  /lab/runs/{run}/bench
  GET  /lab/runs/{run}/conversations
  GET  /lab/runs/{run}/conversations/{sid}?episode=
  GET  /lab/runs/{run}/conversations/{sid}/turns/trace?turn_key=&arm=&rep=
  GET  /lab/runs/{run}/conversations/{sid}/evaluations?arm=
  GET  /lab/runs/{run}/summary?arm=
  GET  /lab/runs/{run}/diff?base=A1&cand=B
  GET  /lab/runs/{run}/report     fidelidad, arena, producción y comparaciones (PR 13)
El `turn_key` va como query (lleva `/`, que no viaja en un segmento de ruta).

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
from src.plugins.chats.shared.turn_view import trace_view
from src.sdk import get_task_queue
from src.sdk.labkit import IMAGE_RE, RUN_ID_RE, LabStorePort, get_lab_store
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


async def _last_status(client: Any) -> dict[str, Any] | None:
    """Cómo terminó la última corrida (el lanzador ya cerró). Si la caja no
    prendió o nunca reportó, no hay progreso en S3: su error solo vive acá."""
    try:
        status = dict(await client.get_workflow_handle(LAB_LAUNCH_WORKFLOW_ID).query("status"))
    except Exception:  # noqa: BLE001 — nunca se lanzó, o ya salió de la retención
        return None
    return status if status.get("phase") else None


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
    client = await _temporal_client()
    status = await _active_status(client)
    return {"active": status, "last": None if status is not None else await _last_status(client)}


@router.post("/lab/runs/active/cancel", status_code=202)
async def cancel_active() -> dict[str, Any]:
    client = await _temporal_client()
    status = await _active_status(client)
    if status is None:
        raise HTTPException(404, detail="No hay una corrida en curso.")
    await client.get_workflow_handle(LAB_LAUNCH_WORKFLOW_ID).signal("cancel")
    return {"cancel_requested": True, "run_id": status.get("run_id")}



# ── Lecturas del contrato lab@v1 (PR 8) ──────────────────────────────────────

_SID_RE = re.compile(r"^wa_[A-Za-z0-9_+]{3,40}$")
_EPISODE_RE = re.compile(r"^ep_\d{1,6}$")
_READ_ARMS = ("A0", *ARMS)


def _run_id(run: str) -> str:
    if not RUN_ID_RE.match(run):
        raise HTTPException(422, detail="Corrida inválida.")
    return run


def _sid(sid: str) -> str:
    if not _SID_RE.match(sid):
        raise HTTPException(422, detail="Conversación inválida.")
    return sid


def _arm(arm: str) -> str:
    if arm not in _READ_ARMS:
        raise HTTPException(422, detail="Brazo inválido: A0, A1, B o C.")
    return arm


def _json_key(store: LabStorePort, key: str, *, missing: str) -> Any:
    raw = store.get_bytes(key)
    if raw is None:
        raise HTTPException(404, detail=missing)
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(502, detail=f"{key} no es JSON válido.") from None


def _jsonl_key(store: LabStorePort, key: str) -> list[dict[str, Any]]:
    raw = store.get_bytes(key)
    if not raw:
        return []
    out = []
    for line in raw.decode("utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


# Una corrida sin fase terminal que no reporta hace más de esto se marca
# vieja: la caja se cayó a mitad y no puede quedar "Corriendo" para siempre.
_STALE_AFTER_MS = 30 * 60_000
_TERMINAL = ("done", "failed", "cancelled")


def _stale(progress: dict[str, Any]) -> bool:
    updated = progress.get("updated_at_ms")
    if progress.get("phase") in _TERMINAL or not isinstance(updated, int):
        return False
    return _now_ms() - updated > _STALE_AFTER_MS


@router.get("/lab/runs")
def list_runs() -> dict[str, Any]:
    store = _store()
    run_ids = store.list_children("runs/")
    runs: list[dict[str, Any]] = []
    for run_id in run_ids:
        manifest = json.loads(store.get_bytes(f"runs/{run_id}/manifest.json") or b"{}")
        progress = json.loads(store.get_bytes(f"runs/{run_id}/progress.json") or b"{}")
        if not manifest and not progress:
            continue
        runs.append(
            {
                "run_id": run_id,
                "bench_id": manifest.get("bench_id"),
                "arms": manifest.get("arms") or [],
                "reps": manifest.get("reps"),
                "registry_version": manifest.get("registry_version"),
                "counts": manifest.get("counts") or {},
                "phase": progress.get("phase"),
                "turns_done": progress.get("turns_done"),
                "turns_total": progress.get("turns_total"),
                "spent_usd": progress.get("spent_usd"),
                "error": progress.get("error"),
                "notes": progress.get("notes") or [],
                "started_at_ms": progress.get("started_at_ms"),
                "updated_at_ms": progress.get("updated_at_ms"),
                "stale": _stale(progress),
            }
        )
    runs.sort(key=lambda r: r.get("started_at_ms") or 0, reverse=True)
    return {"runs": runs}


@router.get("/lab/runs/{run}/bench")
def run_bench(run: str) -> dict[str, Any]:
    return _json_key(_store(), f"runs/{_run_id(run)}/bench_report.json", missing="La corrida no existe o no publicó su banco.")


@router.get("/lab/runs/{run}/conversations")
def run_conversations(run: str) -> dict[str, Any]:
    rows = _json_key(_store(), f"runs/{_run_id(run)}/conversations.json", missing="La corrida no publicó conversaciones.")
    return {"conversations": rows}


def _episode_window(episodes: list[dict[str, Any]], episode_id: str) -> tuple[int, int | None] | None:
    ordered = sorted((e for e in episodes if isinstance(e, dict)), key=lambda e: e.get("started_at_ms") or 0)
    for i, ep in enumerate(ordered):
        if ep.get("episode_id") == episode_id:
            start = int(ep.get("started_at_ms") or 0) - 5_000
            nxt = ordered[i + 1].get("started_at_ms") if i + 1 < len(ordered) else None
            return start, int(nxt) if isinstance(nxt, (int, float)) else None
    return None


def _event_ms(event: dict[str, Any]) -> int | None:
    value = event.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


@router.get("/lab/runs/{run}/conversations/{sid}")
def run_thread(run: str, sid: str, episode: str | None = Query(None, max_length=20)) -> dict[str, Any]:
    thread = _json_key(_store(), f"runs/{_run_id(run)}/threads/{_sid(sid)}.json", missing="Conversación sin hilo en esta corrida.")
    if episode is None:
        return thread
    if not _EPISODE_RE.match(episode):
        raise HTTPException(422, detail="Episodio inválido.")
    window = _episode_window(thread.get("episodes") or [], episode)
    if window is None:
        raise HTTPException(404, detail="Episodio desconocido.")
    start, end = window
    messages = [
        m for m in thread.get("messages") or []
        if (ts := _event_ms(m)) is not None and ts >= start and (end is None or ts < end)
    ]
    turns = [t for t in thread.get("turns") or [] if t.get("episode_id") == episode]
    return {**thread, "episode_id": episode, "messages": messages, "turns": turns}


@router.get("/lab/runs/{run}/conversations/{sid}/turns/trace")
def run_turn_trace(
    run: str,
    sid: str,
    turn_key: str = Query(..., max_length=200),
    arm: str = Query("A0"),
    rep: int = Query(0, ge=0, le=2),
) -> dict[str, Any]:
    traces = _jsonl_key(_store(), f"runs/{_run_id(run)}/turns/{_arm(arm)}/{rep}/{_sid(sid)}.jsonl")
    for trace in traces:
        synthesized = f"{sid}/{trace.get('episode_id')}/t{trace.get('turn')}"
        if turn_key in (trace.get("turn_key"), synthesized):
            return {"arm": arm, "rep": rep, **trace_view(trace)}
    raise HTTPException(404, detail="Ese turno no tiene traza en este brazo.")


@router.get("/lab/runs/{run}/conversations/{sid}/evaluations")
def run_evaluations(run: str, sid: str, arm: str = Query("A0"), rep: int = Query(0, ge=0, le=2)) -> dict[str, Any]:
    records = _jsonl_key(_store(), f"runs/{_run_id(run)}/scores/{_arm(arm)}/{rep}/{_sid(sid)}.jsonl")
    return {"arm": arm, "rep": rep, "episodes": records}


@router.get("/lab/runs/{run}/summary")
def run_summary(run: str, arm: str = Query("A0")) -> dict[str, Any]:
    summary = _json_key(_store(), f"runs/{_run_id(run)}/summary.json", missing="La corrida no publicó su resumen.")
    arm = _arm(arm)
    data = (summary.get("arms") or {}).get(arm)
    if data is None:
        raise HTTPException(404, detail=f"El brazo {arm} todavía no tiene resultados en esta corrida.")
    return data


@router.get("/lab/runs/{run}/diff")
def run_diff(run: str, base: str = Query("A1"), cand: str = Query("B")) -> dict[str, Any]:
    summary = _json_key(_store(), f"runs/{_run_id(run)}/summary.json", missing="La corrida no publicó su resumen.")
    diffs = summary.get("diffs") or {}
    key = f"{_arm(base)}:{_arm(cand)}"
    if key not in diffs:
        raise HTTPException(404, detail=f"Sin comparación {base} → {cand} en esta corrida todavía.")
    return diffs[key]


@router.get("/lab/runs/{run}/report")
def run_report(run: str) -> dict[str, Any]:
    """Lo que la pestaña Resumen muestra además de las gráficas: fidelidad del
    simulador, arena de los bots nuevos, el scorecard de producción de
    referencia y qué comparaciones hay (sus listas de turnos van por /diff)."""
    summary = _json_key(_store(), f"runs/{_run_id(run)}/summary.json", missing="La corrida no publicó su resumen.")
    return {
        "run_id": summary.get("run_id"),
        "mode": summary.get("mode") or "episode",
        "registry_version": summary.get("registry_version"),
        "arms": sorted((summary.get("arms") or {}).keys()),
        "arms_pending": summary.get("arms_pending") or [],
        "production": summary.get("production"),
        "fidelity": summary.get("fidelity"),
        "arena": summary.get("arena") or {},
        "judge": summary.get("judge"),
        "validation": summary.get("validation"),
        "diffs": sorted((summary.get("diffs") or {}).keys()),
    }
