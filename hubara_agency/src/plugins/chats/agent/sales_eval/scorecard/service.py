"""Servicio del scorecard (HU-SC-1) — trayectoria del vault → registro evaluado.

Lo usan la activity del worker `sales_eval` (al cerrar cada episodio) y la API
(recalcular a demanda). Lee el vault; no llama al juez (eso vive en
`judge_checks.py`, async) ni emite métricas (eso es de la activity).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
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
# del turno que lo procesa (debounce + cola del workflow).
_PARTIAL_SLACK_MS = 120_000


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
            "catalog_available": ctx.catalog_available,
        }
    )
    return record
