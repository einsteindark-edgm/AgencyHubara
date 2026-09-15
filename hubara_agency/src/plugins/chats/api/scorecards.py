"""API del scorecard por etapa (HU-SC-2/3/4) — contrato evals@v1, Apéndice A
de `SALES_SCORECARD_PLAN.md`.

Se monta dentro del router de `api/evals.py` (mismo prefijo `/api/chats`), así
que no requiere entrada nueva en el manifest. El dashboard lo consume por el
cast de `agents_admin` (`/api/agents/evals/*`).

  * GET  /evals/checks              → registro de checks (taxonomía)
  * GET  /evals/scorecards          → último scorecard por episodio (lista/matriz)
  * GET  /evals/scorecard           → detalle: scorecard + trayectoria + puntaje legado
  * POST /evals/scorecard/rescore   → recalcula (código ya; juez en el worker)
  * GET  /evals/checks/stats        → Pareto, tendencia semanal, embudo
  * POST /evals/labels              → etiqueta humana de un check
  * GET  /evals/labels              → etiquetas de un episodio
  * GET  /evals/labels/queue        → qué etiquetar primero
  * GET  /evals/calibration         → acuerdo juez vs humano por check

Seguridad: ids validados por regex (anti path traversal); los checks y
veredictos de una etiqueta se validan contra el registro.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from src.plugins.chats.agent.sales_eval.evals import history
from src.plugins.chats.agent.sales_eval.evals.composition import (
    get_eval_history_dir,
    get_vault_dir,
)
from src.plugins.chats.agent.sales_eval.scorecard import (
    calibration,
    catalog_context,
    service,
    stats,
    store,
)
from src.plugins.chats.agent.sales_eval.scorecard.registry import (
    FAMILIES,
    REGISTRY_VERSION,
    SPECS_BY_ID,
    specs_payload,
)
from src.plugins.chats.agent.sales_eval.scorecard.stats import STAGE_ORDER
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^wa_[A-Za-z0-9+]+$")
_EPISODE_ID_RE = re.compile(r"^ep_[0-9]{1,6}$")
_LABEL_VERDICTS = ("pasa", "falla")
_LEGACY_WINDOW_DAYS = 120


def _dates(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def _validate_ids(session_id: str, episode_id: str) -> None:
    # fullmatch: `$` de `match` acepta un salto de línea final.
    if not _SESSION_ID_RE.fullmatch(session_id or "") or not _EPISODE_ID_RE.fullmatch(episode_id or ""):
        raise HTTPException(status_code=400, detail="invalid session_id or episode_id")


def _legacy_score(session_id: str, episode_id: str) -> dict[str, Any] | None:
    data = history.read_conversation_evals(
        get_eval_history_dir(), dates=_dates(_LEGACY_WINDOW_DAYS), suite="online"
    )
    for conv in data.get("conversations") or []:
        if conv.get("session_id") == session_id and conv.get("episode_id") == episode_id:
            evals = conv.get("evals") or []
            return {
                "avg": conv.get("last_avg"),
                "date": conv.get("last_date"),
                "metrics": evals[-1].get("metrics", {}) if evals else {},
            }
    return None


def _previous_judge_results(found: dict[str, Any] | None) -> list[Any]:
    from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult

    if not found:
        return []
    return [
        CheckResult(
            str(r.get("check_id")),
            str(r.get("verdict")),
            turn=r.get("turn") if isinstance(r.get("turn"), int) else None,
            evidence=str(r.get("evidence") or ""),
            critique=str(r.get("critique") or ""),
            source="judge",
        )
        for r in found.get("results") or []
        if isinstance(r, dict) and r.get("source") == "judge" and r.get("check_id") in SPECS_BY_ID
    ]


def _calibration_rows() -> list[dict[str, Any]]:
    # Las etiquetas traen el veredicto del juez que vio el humano: no hace
    # falta releer meses de scorecards por llamada.
    return calibration.compute_calibration((), store.read_labels(store.labels_path(get_vault_dir())))


def _calibrated() -> set[str]:
    return calibration.calibrated_checks(_calibration_rows())


def _judge_verdict_for(found: dict[str, Any] | None, check_id: str) -> str | None:
    for r in (found or {}).get("results") or []:
        if isinstance(r, dict) and r.get("check_id") == check_id and r.get("source") == "judge":
            return str(r.get("verdict")) if r.get("verdict") is not None else None
    return None


def _detail(
    traj: Trajectory, scorecard: dict[str, Any], *, stored: bool
) -> dict[str, Any]:
    row = dict(scorecard)
    row["checks"] = {str(r.get("check_id")): r.get("verdict") for r in row.get("results") or []}
    return {
        "stored": stored,
        "scorecard": row,
        "trajectory": traj.to_dict(),
        "legacy": _legacy_score(traj.session_id, traj.episode_id),
    }


@router.get("/evals/checks")
def scorecard_checks() -> dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "stages": [s for s in STAGE_ORDER if s != "sin_etapa"] + ["transversal"],
        "families": [{"id": k, "label": v[0], "stage": v[1]} for k, v in FAMILIES.items()],
        "checks": specs_payload(),
    }


@router.get("/evals/scorecards")
def list_scorecards(days: int = Query(default=30, ge=1, le=180)) -> dict[str, Any]:
    dates = _dates(days)
    rows = store.list_scorecards(
        store.scorecards_dir(get_vault_dir()), dates=dates, episode_since=dates[-1]
    )
    return {"days": days, "count": len(rows), "registry_version": REGISTRY_VERSION, "scorecards": rows}


@router.get("/evals/scorecard")
async def get_scorecard(
    session_id: str = Query(..., max_length=120),
    episode_id: str = Query(..., max_length=20),
) -> dict[str, Any]:
    _validate_ids(session_id, episode_id)
    vault = get_vault_dir()
    traj = service.load_trajectory(vault, session_id, episode_id)
    found = store.find_latest(store.scorecards_dir(vault), session_id, episode_id)
    if found is not None:
        return _detail(traj, found, stored=True)
    ctx = await catalog_context.build_check_context()
    record = service.score_trajectory(traj, ctx, calibrated=_calibrated())
    return _detail(traj, record, stored=False)


async def _start_judge_workflow(session_id: str, episode_id: str) -> str:
    """Arranca `ScoreEpisodeWorkflow` en el worker `sales_eval` (juez incluido)."""
    from src.plugins.chats.agent.sales_eval.evals.contracts import ScoreEpisodeInput
    from src.sdk import get_task_queue
    from src.sdk.runtime import get_temporal_client

    from temporalio.exceptions import WorkflowAlreadyStartedError

    client = await get_temporal_client()
    # Id estable por episodio: un doble clic no paga dos veces el juez; si ya
    # hay un recálculo en vuelo, se reusa.
    workflow_id = f"scorecard-{session_id}-{episode_id}"
    try:
        await client.start_workflow(
            "ScoreEpisodeWorkflow",
            ScoreEpisodeInput(session_id=session_id, episode_id=episode_id, with_judge=True),
            id=workflow_id,
            task_queue=get_task_queue("chats", "sales_eval"),
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id


@router.post("/evals/scorecard/rescore")
async def rescore(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    session_id = str(body.get("session_id") or "")
    episode_id = str(body.get("episode_id") or "")
    _validate_ids(session_id, episode_id)
    vault = get_vault_dir()
    cards_dir = store.scorecards_dir(vault)
    traj = service.load_trajectory(vault, session_id, episode_id)
    previous = store.find_latest(cards_dir, session_id, episode_id)
    ctx = await catalog_context.build_check_context()
    # Recalcular sin juez conserva los resultados del juez anteriores: un
    # recálculo de código no debe borrar el juicio ya hecho.
    record = store.append_scorecard(
        cards_dir,
        service.score_trajectory(
            traj, ctx, judge_results=_previous_judge_results(previous), calibrated=_calibrated()
        ),
    )
    detail = _detail(traj, record, stored=True)
    if body.get("judge"):
        try:
            detail["judge_workflow_id"] = await _start_judge_workflow(session_id, episode_id)
            detail["judge_queued"] = True
        except Exception as exc:  # noqa: BLE001 — sin Temporal, el recálculo de código igual sirve
            detail["judge_queued"] = False
            detail["judge_error"] = repr(exc)[:200]
    return detail


@router.get("/evals/checks/stats")
def check_stats(days: int = Query(default=56, ge=7, le=365)) -> dict[str, Any]:
    dates = _dates(days)
    rows = store.list_scorecards(
        store.scorecards_dir(get_vault_dir()), dates=dates, episode_since=dates[-1]
    )
    weeks = stats.weeks_between(dates[-1], dates[0])
    return {"days": days, **stats.compute_stats(rows, weeks=weeks)}


@router.post("/evals/labels")
def create_label(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    session_id = str(body.get("session_id") or "")
    episode_id = str(body.get("episode_id") or "")
    _validate_ids(session_id, episode_id)
    check_id = str(body.get("check_id") or "")
    verdict = str(body.get("verdict") or "")
    if check_id not in SPECS_BY_ID:
        raise HTTPException(status_code=400, detail="unknown check_id")
    if verdict not in _LABEL_VERDICTS:
        raise HTTPException(status_code=400, detail="verdict must be pasa or falla")
    vault = get_vault_dir()
    found = store.find_latest(store.scorecards_dir(vault), session_id, episode_id)
    label = store.append_label(
        store.labels_path(vault),
        {
            "session_id": session_id,
            "episode_id": episode_id,
            "check_id": check_id,
            "verdict": verdict,
            "note": str(body.get("note") or "")[:500],
            # Lo que el juez dijo cuando el humano etiquetó: la calibración
            # compara contra ESE veredicto, sin releer los scorecards.
            "judge_verdict": _judge_verdict_for(found, check_id),
        },
    )
    return {"ok": True, "label": label}


@router.get("/evals/labels")
def list_labels(
    session_id: str = Query(..., max_length=120),
    episode_id: str = Query(..., max_length=20),
) -> dict[str, Any]:
    _validate_ids(session_id, episode_id)
    return {
        "labels": store.read_labels(
            store.labels_path(get_vault_dir()), session_id=session_id, episode_id=episode_id
        )
    }


@router.get("/evals/labels/queue")
def label_queue(
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    vault = get_vault_dir()
    records = store.read_scorecards(store.scorecards_dir(vault), dates=_dates(days))
    items = calibration.label_queue(records, store.read_labels(store.labels_path(vault)), limit=limit)
    return {"items": items}


@router.get("/evals/calibration")
def judge_calibration() -> dict[str, Any]:
    return {
        "min_labels": calibration.MIN_LABELS,
        "kappa_threshold": calibration.KAPPA_THRESHOLD,
        "checks": _calibration_rows(),
    }
