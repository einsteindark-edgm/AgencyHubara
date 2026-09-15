"""Servicio del scorecard (HU-SC-1) — trayectoria del vault → registro evaluado.

Lo usan la activity del worker `sales_eval` (al cerrar cada episodio) y la API
(recalcular a demanda). Lee el vault; no llama al juez (eso vive en
`judge_checks.py`, async) ni emite métricas (eso es de la activity).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.evals import reconstruct
from src.plugins.chats.agent.sales_eval.scorecard.engine import run_code_checks
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    Trajectory,
    build_legacy_trajectory,
    build_trajectory,
)
from src.plugins.chats.agent.sales_eval.scorecard.verdict import compute_scorecard
from src.plugins.chats.shared import turn_traces


def load_trajectory(vault_dir: Path, session_id: str, episode_id: str) -> Trajectory:
    """Trayectoria del episodio: trazas por turno si existen; si no, legado."""
    metadata = reconstruct.read_session_metadata(vault_dir, session_id)
    episode = reconstruct.find_episode(metadata, episode_id) or {"episode_id": episode_id}
    traces = turn_traces.traces_for_episode(vault_dir, session_id, episode_id)
    events, _ = reconstruct.read_episode_events(vault_dir, session_id, episode_id)
    if not traces:
        return build_legacy_trajectory(events, session_id=session_id, episode=episode)
    traj = build_trajectory(traces, session_id=session_id, episode=episode)
    if _started_before_traces(events, traces):
        return replace(traj, fidelity="partial")
    return traj


# Tolerancia entre el mensaje del cliente (timestamp del ingest) y el inicio
# del turno que lo procesa: debounce + cola del workflow y, sobre todo, un
# worker reiniciado por un deploy (~5 min) que procesa el mensaje pendiente al
# volver. Un episodio anterior a la traza empezó horas o días antes.
_PARTIAL_SLACK_MS = 10 * 60_000


def _started_before_traces(events: list[dict[str, Any]], traces: list[dict[str, Any]]) -> bool:
    """El episodio tiene mensajes del cliente anteriores a la primera traza.

    Pasa con episodios que arrancaron antes del deploy de `turn-trace-v1`: la
    trayectoria tendría solo los últimos turnos. Se marca `partial` para que
    los checks que dependen de la traza devuelvan `desconocido`.
    """
    starts = [t.get("turn_started_ms") for t in traces if isinstance(t.get("turn_started_ms"), (int, float))]
    if not starts:
        return False
    first = min(starts)
    for ev in events:
        if ev.get("role") != "user":
            continue
        ts = reconstruct._event_ts_ms(ev)
        if ts is not None and ts < first - _PARTIAL_SLACK_MS:
            return True
    return False


# ── Espera de la traza del turno de cierre ──────────────────────────────────
# El workflow de ventas despacha `EpisodeClosedEvent` ANTES de enviar el último
# mensaje, hacer el flush y persistir la traza del turno (sales_session.py). Si
# el scorecard corre antes de que esa traza aterrice, evalúa el episodio sin su
# turno de cierre y reprueba en falso (CIE-04, TAG-01, CON-01…). La espera fija
# del workflow (90 s) no alcanza cuando el send reintenta. Solo aplica a
# cierres recientes: un cierre viejo no tiene turno en vuelo.
CLOSING_TRACE_TIMEOUT_S = 180.0
_CLOSING_TRACE_POLL_S = 5.0
_RECENT_CLOSURE_MS = 15 * 60 * 1000


async def await_closing_trace(
    vault_dir: Path,
    session_id: str,
    episode_id: str,
    *,
    timeout_s: float = CLOSING_TRACE_TIMEOUT_S,
    poll_s: float = _CLOSING_TRACE_POLL_S,
    now_ms: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """True si no hay turno de cierre que esperar (episodio abierto, cierre
    viejo) o si su traza ya aterrizó; False si venció el plazo (se evalúa
    igual, avisando). El cierre ocurre DENTRO del turno, antes de la traza, así
    que la traza del turno de cierre tiene `recorded_at_ms >= closed_at_ms`."""
    metadata = reconstruct.read_session_metadata(vault_dir, session_id)
    episode = reconstruct.find_episode(metadata, episode_id) or {}
    closed_at = episode.get("closed_at_ms")
    if not isinstance(closed_at, (int, float)):
        return True
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if now - closed_at > _RECENT_CLOSURE_MS:
        return True
    deadline = time.monotonic() + timeout_s
    while True:
        traces = turn_traces.traces_for_episode(vault_dir, session_id, episode_id)
        if any(
            isinstance(t.get("recorded_at_ms"), (int, float)) and t["recorded_at_ms"] >= closed_at
            for t in traces
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        await sleep(poll_s)


def episode_date(traj: Trajectory) -> str | None:
    """Fecha UTC del episodio (cierre, o inicio si sigue abierto). Es la fecha
    por la que se ventanean listas y tendencias; `None` sin timestamps."""
    at = traj.closed_at_ms or traj.started_at_ms
    if not isinstance(at, int) or at <= 0:
        return None
    return datetime.fromtimestamp(at / 1000, timezone.utc).date().isoformat()


def score_trajectory(
    traj: Trajectory,
    ctx: CheckContext,
    *,
    judge_results: Iterable[CheckResult] = (),
    calibrated: Iterable[str] = frozenset(),
) -> dict[str, Any]:
    """Checks de código + resultados del juez (si corrió) → registro del scorecard."""
    judged = list(judge_results)
    results = [*run_code_checks(traj, ctx), *judged]
    card = compute_scorecard(traj, SPECS_BY_ID, results, calibrated=calibrated)
    record = card.to_dict()
    record.update(
        {
            "judge": bool(judged),
            "registry_version": REGISTRY_VERSION,
            "order_id": traj.order_id,
            "episode_date": episode_date(traj),
            "catalog_available": ctx.catalog_available,
        }
    )
    return record
