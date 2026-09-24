"""Servicio del scorecard (HU-SC-1) — trayectoria del vault → registro evaluado.

Lo usan la activity del worker `sales_eval` (al cerrar cada episodio) y la API
(recalcular a demanda). Lee el vault; no llama al juez (eso vive en
`judge_checks.py`, async) ni emite métricas (eso es de la activity).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.evals import reconstruct
from src.plugins.chats.agent.sales_eval.scorecard.engine import pin_to_focus, run_code_checks
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    Trajectory,
    Turn,
    build_legacy_trajectory,
    build_trajectory,
    focus_trajectory,
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


# Epoch ms del 2020-01-01: un timestamp menor no es una fecha real (época
# chica de una fixture, segundos en vez de ms) y no debe fechar el episodio.
_MIN_PLAUSIBLE_MS = 1_577_836_800_000


def episode_date(traj: Trajectory) -> str | None:
    """Fecha UTC del episodio (cierre, o inicio si sigue abierto). Es la fecha
    por la que se ventanean listas y tendencias; `None` sin timestamps
    plausibles (entonces los consumidores usan la fecha de evaluación)."""
    at = traj.closed_at_ms or traj.started_at_ms
    if not isinstance(at, int) or at < _MIN_PLAUSIBLE_MS:
        return None
    return datetime.fromtimestamp(at / 1000, timezone.utc).date().isoformat()


def daily_scorecard_units(
    vault_dir: Path, window: Any, *, now_ms: int | None = None
) -> list[str]:
    """Episodios que el barrido diario debe calificar (unit ids `sesión::episodio`).

    Parte de la misma selección de la eval diaria (sesiones con actividad en la
    ventana, episodios activos o cerrados en ella) pero sin mínimo de turnos, y
    se queda con:
      * episodios ABIERTOS: nunca emiten cierre (INTERESADO, ruta humano) y su
        trayectoria creció en la ventana;
      * episodios CERRADOS sin un scorecard posterior al cierre (el disparo al
        cierre falló o no existía).
    Episodios sin mensajes del cliente (solo remarketing o notificaciones) no
    son del asesor y quedan fuera.
    """
    from dataclasses import replace as dc_replace

    from src.plugins.chats.agent.sales_eval.evals import select
    from src.plugins.chats.agent.sales_eval.scorecard import store

    now = time.time() if now_ms is None else now_ms / 1000
    units = select.select_eval_units(dc_replace(window, min_turns=1), vault_dir=vault_dir, now=now)
    cards_dir = store.scorecards_dir(vault_dir)
    out: list[str] = []
    for unit in units:
        session_id, episode_id = reconstruct.parse_eval_unit_id(unit)
        if not episode_id:
            continue
        events, episode = reconstruct.read_episode_events(vault_dir, session_id, episode_id)
        if not any(isinstance(e, dict) and e.get("role") == "user" for e in events):
            continue
        closed_at = (episode or {}).get("closed_at_ms")
        if isinstance(closed_at, (int, float)):
            found = store.find_latest(cards_dir, session_id, episode_id)
            if found is not None and _ts_ms(found.get("ts")) >= closed_at:
                continue
        out.append(unit)
    return out


def _ts_ms(value: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(value)).timestamp() * 1000)
    except ValueError:
        return 0


def score_trajectory(
    traj: Trajectory,
    ctx: CheckContext,
    *,
    judge_results: Iterable[CheckResult] = (),
    calibrated: Iterable[str] = frozenset(),
) -> dict[str, Any]:
    """Checks de código + resultados del juez (si corrió) → registro del scorecard."""
    from src.plugins.chats.agent.sales_eval.scorecard.judge_checks import is_judge_error

    judged = list(judge_results)
    judge_errors = sum(1 for r in judged if is_judge_error(r))
    results = [*run_code_checks(traj, ctx), *judged]
    card = compute_scorecard(traj, SPECS_BY_ID, results, calibrated=calibrated)
    record = card.to_dict()
    record.update(
        {
            # El juez "corrió" solo si alguna llamada devolvió un juicio: un 429
            # en todas las llamadas no es un scorecard con juez.
            "judge": len(judged) > judge_errors,
            "judge_errors": judge_errors,
            "registry_version": REGISTRY_VERSION,
            "order_id": traj.order_id,
            "episode_date": episode_date(traj),
            "catalog_available": ctx.catalog_available,
        }
    )
    return record



# ── Modo turno (laboratorio, plan §5.2–5.5) ─────────────────────────────────
# Precedencia al agregar un check sobre varios turnos: la señal más fuerte gana.
_AGGREGATE_RANK = {"falla": 0, "pasa": 1, "desconocido": 2, "sin_senal": 3, "no_aplica": 4}


def score_turns(
    real: Trajectory,
    candidates: Mapping[int, Turn],
    ctx: CheckContext,
    *,
    episodes_at: Mapping[int, dict[str, Any]] | None = None,
    judge_results: Mapping[int, Iterable[CheckResult]] | None = None,
    calibrated: Iterable[str] = frozenset(),
) -> dict[str, Any]:
    """Califica turnos simulados con el prefijo REAL como contexto.

    Cada candidato (turno k → `Turn`, misma forma que `turn_from_trace`) se
    juzga sobre `focus_trajectory(real, candidato)`: solo ese turno puede
    fallar. `episodes_at[k]` es el episodio al momento del turno (sin cierre);
    `judge_results[k]` los checks de juez de ese turno
    (`judge_checks.run_judge_checks_focus`). El veredicto del brazo simulado
    sale de la misma regla (`verdict.compute_scorecard`) sobre la unión de los
    checks por turno.
    """
    calibrated_set = frozenset(calibrated)
    by_turn: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    union: list[CheckResult] = []
    for k in sorted(candidates):
        focus = focus_trajectory(real, candidates[k], episode_at=(episodes_at or {}).get(k))
        judged = pin_to_focus((judge_results or {}).get(k, ()), k)
        results = [*run_code_checks(focus, ctx), *judged]
        card = compute_scorecard(focus, SPECS_BY_ID, results, calibrated=calibrated_set)
        by_turn.append(
            {
                "turn": k,
                "verdict": card.verdict,
                "counts": card.counts,
                "first_failure": card.first_failure,
                "results": card.results,
            }
        )
        rows.extend(card.results)
        union.extend(results)
    simulated = replace(
        real,
        turns=tuple(candidates[k] for k in sorted(candidates)),
        closing_tag=None,
        closing_motivo=None,
        closed_at_ms=None,
    )
    episode = compute_scorecard(simulated, SPECS_BY_ID, union, calibrated=calibrated_set)
    return {
        "mode": "turn",
        "registry_version": REGISTRY_VERSION,
        "session_id": real.session_id,
        "episode_id": real.episode_id,
        "by_turn": by_turn,
        "verdict": episode.verdict,
        "counts": episode.counts,
        "first_failure": episode.first_failure,
        "first_critical": episode.first_critical,
        "results": rows,
    }


def aggregate_checks(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Un veredicto por check sobre varios turnos: falla > pasa > desconocido >
    sin_senal > no_aplica (para estadísticas; `store.to_row` se queda con el
    último, que en modo turno no dice nada)."""
    out: dict[str, str] = {}
    for row in rows:
        check_id, verdict = str(row.get("check_id")), str(row.get("verdict"))
        rank = _AGGREGATE_RANK.get(verdict, 9)
        if check_id not in out or rank < _AGGREGATE_RANK.get(out[check_id], 9):
            out[check_id] = verdict
    return out
