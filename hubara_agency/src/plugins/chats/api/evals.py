"""API de curación de goldens — driving adapter HTTP del harness de evals.

Expone el buffer de candidatos a golden (escritos por la auto-curación del agente
`sales_eval`) para que el operador los revise, edite el `expected_outcome` y
apruebe/descarte desde el dashboard (pestaña "Calidad LLM").

Flujo:
  * GET    /evals/candidates          → lista (resumen) de candidatos pendientes.
  * GET    /evals/candidates/{id}     → candidato completo (turns + scores + draft).
  * POST   /evals/candidates/{id}/approve → fija expected_outcome (editado) +
            status=human_reviewed y mueve a `approved/` (durable; el operador lo
            mergea a tests/evals/.../curated.json y commitea).
  * DELETE /evals/candidates/{id}     → descarta el candidato.

R-DIP: la API (chats) importa el composition del propio agente chats (`sales_eval`)
— intra-plugin, permitido. `get_candidates_dir` no importa deepeval (lazy), así que
este router es importable al boot de FastAPI sin el extra `evals`.

Seguridad: `id` se sanitiza contra path traversal (resuelto + contenido dentro del
dir de candidatos).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from src.plugins.chats.agent.sales_eval.evals import history
from src.plugins.chats.agent.sales_eval.evals.composition import (
    get_candidates_dir,
    get_eval_history_dir,
)

router = APIRouter()

_APPROVED_SUBDIR = "approved"


def _candidates_dir() -> Path:
    return get_candidates_dir()


def _safe_path(candidate_id: str) -> Path:
    """Resuelve `<dir>/<id>.json` y verifica que quede DENTRO del dir (anti-traversal)."""
    base = _candidates_dir().resolve()
    target = (base / f"{candidate_id}.json").resolve()
    if base not in target.parents:
        raise HTTPException(status_code=400, detail="invalid candidate id")
    return target


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="candidate not found or corrupt")


def _summary(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("additional_metadata", {}) or {}
    all_scores = meta.get("all_scores", []) or []
    avg = (
        round(sum(s.get("score", 0.0) for s in all_scores) / len(all_scores), 3)
        if all_scores
        else None
    )
    return {
        "id": path.stem,
        "scenario": data.get("scenario", ""),
        "status": meta.get("status", "needs_human_review"),
        "source": meta.get("source", ""),
        "num_turns": len(data.get("turns", []) or []),
        "avg_score": avg,
        "failed_metrics": [m.get("metric") for m in (meta.get("failed_metrics") or [])],
        "expected_outcome_preview": (data.get("expected_outcome", "") or "")[:200],
    }


@router.get("/evals/history")
def eval_history(days: int = 30, suite: str = "online") -> dict[str, Any]:
    """Tendencia de scores por métrica en el tiempo (para el frontend Calidad LLM).

    Agrega el histórico (un JSONL por día que escribe el eval) en una serie por
    métrica: por cada día, avg/min/n/n_below(0.7). Así el front dibuja "tal fecha
    la métrica estuvo bajo, tal otra fecha mejoró/empeoró". `suite` = online|golden.
    """
    from datetime import datetime, timedelta, timezone

    days = max(1, min(days, 180))
    today = datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    return history.read_trend(get_eval_history_dir(), dates=dates, suite=suite)


@router.get("/evals/candidates")
def list_candidates() -> dict[str, Any]:
    """Lista (resumen) de candidatos pendientes de revisión humana."""
    d = _candidates_dir()
    items: list[dict[str, Any]] = []
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                items.append(_summary(f, json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return {"candidates": items, "count": len(items)}


@router.get("/evals/candidates/{candidate_id}")
def get_candidate(candidate_id: str) -> dict[str, Any]:
    """Candidato completo: turns (PII redactada) + scores + expected_outcome draft."""
    path = _safe_path(candidate_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="candidate not found")
    return _read(path)


@router.post("/evals/candidates/{candidate_id}/approve")
def approve_candidate(
    candidate_id: str, payload: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, Any]:
    """Aprueba un candidato: fija expected_outcome (editado) + status, y lo mueve a
    `approved/`. El operador mergea `approved/*.json` a curated.json y commitea."""
    path = _safe_path(candidate_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="candidate not found")
    data = _read(path)

    edited = payload.get("expected_outcome")
    if isinstance(edited, str) and edited.strip():
        data["expected_outcome"] = edited.strip()
    data.setdefault("additional_metadata", {})["status"] = "human_reviewed"

    approved_dir = _candidates_dir() / _APPROVED_SUBDIR
    approved_dir.mkdir(parents=True, exist_ok=True)
    dest = approved_dir / path.name
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.unlink()
    except OSError:
        pass
    return {"status": "approved", "id": candidate_id, "moved_to": str(dest)}


@router.delete("/evals/candidates/{candidate_id}")
def discard_candidate(candidate_id: str) -> dict[str, Any]:
    """Descarta (borra) un candidato a golden."""
    path = _safe_path(candidate_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="candidate not found")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not delete: {exc}")
    return {"status": "discarded", "id": candidate_id}
