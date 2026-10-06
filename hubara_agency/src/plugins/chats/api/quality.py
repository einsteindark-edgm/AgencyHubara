"""Calidad LLM con la vista del laboratorio, sobre producción (evals@v1, 2026-10-02).

El operador reemplazó la vista de Calidad LLM por la del laboratorio. Estas
rutas sirven lo que el laboratorio publica de su banco, armado del vault de
producción (`sales_eval/quality_view.py`), con las formas del laboratorio sin
brazos ni repeticiones. Agents las consume por su cast (`/api/agents/evals/
production/*`).

  GET /evals/production/conversations?days&bot        la lista (veredicto y bot por episodio)
  GET /evals/production/conversations/{sid}?episode    el hilo (mensajes y turnos con su ráfaga)
  GET …/{sid}/turns/trace?turn_key                     la ventana del turno, con las decisiones de Jev
  GET …/{sid}/evaluations                              cada episodio turno por turno
  GET /evals/production/jev?days&bot                   el informe de Jev (por defecto, del bot Jev)

La lista y el informe salen de los scorecards de la ventana (por la fecha del
episodio) y leen las trazas de cada conversación UNA vez (el cast corta a
los 15 s).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.plugins.chats.agent.sales_eval import quality_view
from src.plugins.chats.agent.sales_eval.evals.composition import get_vault_dir
from src.plugins.chats.agent.sales_eval.scorecard import catalog_context, service, store
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.turn_view import trace_view, turn_key_of

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^wa_[A-Za-z0-9+]+$")
_EPISODE_PATTERN = r"^ep_[0-9]{1,6}$"
_BOT_PATTERN = "^(actual|nuevo)$"
#: Días de archivos de scorecards que se recorren para hallar los de una
#: conversación (como `store.find_latest`).
_SESSION_CARD_FILES = 120


def _dates(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def _sid(session_id: str) -> str:
    # fullmatch: `$` de `match` acepta un salto de línea final.
    if not _SESSION_ID_RE.fullmatch(session_id or ""):
        raise HTTPException(status_code=422, detail="Conversación inválida.")
    return session_id


def _window(days: int, bot: str | None) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Los scorecards de la ventana (por la fecha del episodio) del bot pedido,
    y las trazas de sus conversaciones (leídas una vez)."""
    vault = get_vault_dir()
    dates = _dates(days)
    rows = store.list_scorecards(store.scorecards_dir(vault), dates=dates, episode_since=dates[-1])
    traces: dict[str, list[dict[str, Any]]] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        sid, ep = str(row.get("session_id") or ""), str(row.get("episode_id") or "")
        if not _SESSION_ID_RE.fullmatch(sid):
            continue
        if sid not in traces:
            traces[sid] = turn_traces.read_traces(vault, sid)
        if bot is None or episode_bot(t for t in traces[sid] if t.get("episode_id") == ep) == bot:
            kept.append(row)
    return kept, traces


@router.get("/evals/production/conversations")
def conversations(
    days: int = Query(default=56, ge=1, le=180),
    bot: str | None = Query(default=None, pattern=_BOT_PATTERN),
) -> dict[str, Any]:
    rows, traces = _window(days, bot)
    found = quality_view.conversation_rows(rows, traces)
    return {"days": days, "bot": bot, "count": len(found), "conversations": found}


@router.get("/evals/production/conversations/{session_id}")
def conversation_thread(
    session_id: str, episode: str | None = Query(default=None, pattern=_EPISODE_PATTERN)
) -> dict[str, Any]:
    thread = quality_view.production_thread(get_vault_dir(), _sid(session_id))
    if episode is None:
        return thread
    sliced = quality_view.episode_slice(thread, episode)
    if sliced is None:
        raise HTTPException(status_code=404, detail="Episodio desconocido.")
    return sliced


@router.get("/evals/production/conversations/{session_id}/turns/trace")
def turn_trace(session_id: str, turn_key: str = Query(..., max_length=200)) -> dict[str, Any]:
    vault = get_vault_dir()
    sid = _sid(session_id)
    traces = turn_traces.read_traces(vault, sid)
    for trace in traces:
        if isinstance(trace, dict) and turn_key_of(trace, sid) == turn_key:
            view = trace_view(trace)
            decisions = quality_view.turn_decisions(
                traces, quality_view.read_session_decisions(vault, sid), turn_key, session_id=sid
            )
            return {**view, "trace": {**view["trace"], "decisions": decisions}}
    raise HTTPException(status_code=404, detail="Ese turno no tiene traza.")


def _session_cards(session_id: str) -> dict[str, dict[str, Any]]:
    """El último scorecard de cada episodio de la conversación (recorre los
    archivos una sola vez)."""
    directory = store.scorecards_dir(get_vault_dir())
    try:
        files = sorted(directory.glob("*.jsonl"), reverse=True)[:_SESSION_CARD_FILES]
    except OSError:
        return {}
    records = [r for path in files for r in store.read_scorecards(directory, dates=[path.stem]) if r.get("session_id") == session_id]
    return {str(r.get("episode_id")): r for r in store.latest_by_unit(records)}


@router.get("/evals/production/conversations/{session_id}/evaluations")
async def conversation_evaluations(session_id: str) -> dict[str, Any]:
    """Cada episodio con trazas, turno por turno: el scorecard guardado si ya
    es de la vista del laboratorio (`mode: turn`); si no (sin calificar, o de
    la vista de antes), se califica al vuelo con el código (sin juez)."""
    vault = get_vault_dir()
    sid = _sid(session_id)
    episodes = sorted({str(t.get("episode_id")) for t in turn_traces.read_traces(vault, sid) if t.get("episode_id")})
    cards = _session_cards(sid)
    out: list[dict[str, Any]] = []
    ctx = None
    for episode_id in episodes:
        card = cards.get(episode_id)
        if card is None or card.get("mode") != "turn":
            if ctx is None:
                ctx = await catalog_context.build_check_context()
            traj, states = service.episode_inputs(vault, sid, episode_id)
            card = service.score_episode_turns(traj, ctx, states=states)
        out.append(card)
    return {"session_id": sid, "episodes": out}


@router.get("/evals/production/jev")
def jev(
    days: int = Query(default=56, ge=1, le=180),
    bot: str = Query(default="nuevo", pattern=_BOT_PATTERN),
) -> dict[str, Any]:
    rows, _traces = _window(days, bot)
    units = [(str(r["session_id"]), str(r["episode_id"])) for r in rows]
    return {"days": days, "bot": bot, **quality_view.jev_report(get_vault_dir(), units)}
