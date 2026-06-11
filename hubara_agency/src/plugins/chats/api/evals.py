"""API del harness de evals — driving adapter HTTP (pestaña "Calidad LLM").

Expone las tres vistas del eval loop:
  * GET    /evals/history             → tendencia agregada por métrica por día.
  * GET    /evals/conversations       → evaluaciones POR EPISODIO: qué conversación
            puntuó qué, su timeline de evals (¿mejoró o quedó igual?) y si es
            candidata a golden (cross-ref con el buffer de curación).
  * GET    /evals/transcript          → la conversación del episodio (PII redactada,
            el MISMO segmento que vio el juez) para auditar un score.
  * GET    /evals/candidates          → lista (resumen) de candidatos pendientes.
  * GET    /evals/candidates/{id}     → candidato completo (turns + scores + draft).
  * POST   /evals/candidates/{id}/approve → fija expected_outcome (editado) +
            status=human_reviewed y mueve a `approved/` (durable; el operador lo
            mergea a tests/evals/.../curated.json y commitea).
  * DELETE /evals/candidates/{id}     → descarta el candidato.

R-DIP: la API (chats) importa el composition del propio agente chats (`sales_eval`)
— intra-plugin, permitido. Nada de este módulo importa deepeval (los imports del
paquete evals son lazy), así que el router es importable al boot de FastAPI sin el
extra `evals`.

Seguridad: `id`/`session_id` se sanitizan contra path traversal (regex + resuelto
dentro del dir correspondiente).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from src.plugins.chats.agent.sales_eval.evals import history, reconstruct
from src.plugins.chats.agent.sales_eval.evals.composition import (
    get_candidates_dir,
    get_eval_history_dir,
    get_vault_dir,
)

router = APIRouter()

_APPROVED_SUBDIR = "approved"

_SESSION_ID_RE = re.compile(r"^wa_[A-Za-z0-9+]+$")
_EPISODE_ID_RE = re.compile(r"^ep_[0-9]{1,6}$")


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
        "session_id": meta.get("source_session_redacted", "") or "",
        "episode_id": meta.get("source_episode", "") or "",
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


def _candidate_index() -> dict[tuple[str, str], dict[str, str]]:
    """Mapa (session_id, episode_id) → {id, status} de los candidatos en disco.

    `pending` = en el buffer esperando revisión humana; `approved` = movido a
    `approved/` (listo para mergear a curated.json). Permite que la vista por
    conversación muestre el estado de curación sin que el frontend haga N calls.
    """
    index: dict[tuple[str, str], dict[str, str]] = {}
    base = _candidates_dir()
    for status, d in (("approved", base / _APPROVED_SUBDIR), ("pending", base)):
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                meta = json.loads(f.read_text(encoding="utf-8")).get(
                    "additional_metadata", {}
                ) or {}
            except (OSError, json.JSONDecodeError):
                continue
            session_id = str(meta.get("source_session_redacted", "") or "")
            episode_id = str(meta.get("source_episode", "") or "")
            if session_id:
                # pending (2ª pasada) pisa approved: si reapareció en el buffer,
                # lo operativo es que hay una revisión pendiente.
                index[(session_id, episode_id)] = {"id": f.stem, "status": status}
    return index


@router.get("/evals/conversations")
def eval_conversations(
    days: int = Query(default=7, ge=1, le=90),
    suite: str = Query(default="online"),
) -> dict[str, Any]:
    """Evaluaciones agrupadas por conversación (sesión + episodio).

    Responde lo que la tendencia agregada no puede: QUÉ episodio generó los malos
    scores, su timeline de evals (¿subió el puntaje o quedó igual?), las métricas
    falladas con la razón del juez, y el estado de curación del candidato a golden
    asociado. El frontend "Calidad LLM" arma su vista central con esto.
    """
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    out = history.read_conversation_evals(
        get_eval_history_dir(), dates=dates, suite=suite
    )

    candidates = _candidate_index()
    vault = get_vault_dir()
    meta_cache: dict[str, dict[str, Any]] = {}
    for conv in out["conversations"]:
        sid, eid = conv["session_id"], conv["episode_id"]
        cand = candidates.get((sid, eid))
        conv["candidate_id"] = cand["id"] if cand else ""
        conv["candidate_status"] = cand["status"] if cand else ""
        # Contexto del episodio (closing_tag/orden) para que la lista sea legible.
        conv["closing_tag"] = None
        conv["order_id"] = None
        if eid:
            if sid not in meta_cache:
                meta_cache[sid] = reconstruct.read_session_metadata(vault, sid)
            ep = reconstruct.find_episode(meta_cache[sid], eid)
            if ep:
                conv["closing_tag"] = ep.get("closing_tag")
                conv["order_id"] = ep.get("order_id")
    return out


@router.get("/evals/transcript")
def eval_transcript(
    session_id: str = Query(..., min_length=1, max_length=120),
    episode_id: str = Query(default=""),
) -> dict[str, Any]:
    """La conversación del episodio tal como la vio el juez (PII redactada).

    Mismo pipeline de reconstrucción que la evaluación (slice por episodio +
    corte en takeover humano + redacción), para que el operador audite el score
    contra EXACTAMENTE lo que se puntuó. `episode_id` vacío = sesión entera
    (evaluaciones legacy).
    """
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session id")
    if episode_id and not _EPISODE_ID_RE.match(episode_id):
        raise HTTPException(status_code=400, detail="invalid episode id")

    vault = get_vault_dir()
    events, episode = reconstruct.read_episode_events(vault, session_id, episode_id)
    if not events:
        raise HTTPException(status_code=404, detail="conversation not found")

    turns = reconstruct.to_evaluable_turns(
        events, redact=True, stop_at_human_takeover=True
    )
    truncated = any(ev.get("sender") == "human" for ev in events)
    return {
        "session_id": session_id,
        "episode_id": episode_id,
        "turns": turns,
        "truncated_at_human_takeover": truncated,
        "episode": (
            {
                "closing_tag": episode.get("closing_tag"),
                "closing_motivo": episode.get("closing_motivo"),
                "started_at_ms": episode.get("started_at_ms"),
                "closed_at_ms": episode.get("closed_at_ms"),
                "order_id": episode.get("order_id"),
            }
            if episode
            else None
        ),
    }


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
