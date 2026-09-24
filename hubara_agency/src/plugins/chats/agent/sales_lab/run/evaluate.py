"""Evaluación de un brazo del laboratorio (plan §5.2–5.4, PR 13).

El MISMO scorecard de producción, en modo turno (`service.score_turns`): por
episodio del banco, cada turno simulado se juzga con el prefijo REAL como
contexto y solo ese turno puede fallar. A0 (lo que pasó) se re-mide igual,
con sus propios turnos como candidatos: así las columnas A0 · A1 · B · C se
comparan con la misma regla.

  * La trayectoria real sale de las trazas del banco (`vault/<sesión>/evals`).
  * El episodio "al momento" de cada turno es el del caso (`episodes_at`):
    nunca el cierre, que es del futuro, y con la orden del inicio del turno
    (`state_before`), no la final.
  * El complemento del bot nuevo (segundo turno de sistema) es parte de la
    respuesta de su turno.
  * Un turno sin resultado simulado (el caso falló) queda fuera y se anota
    (`missing_turns`): nunca se inventa.
  * El juez (si hay) corre UNA vez por check y episodio con las candidatas
    marcadas (`run_judge_checks_focus`).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.judge_checks import is_judge_error, run_judge_checks_focus
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    Trajectory,
    Turn,
    build_trajectory,
    focus_trajectory,
    turn_from_trace,
)
from src.plugins.chats.shared import turn_traces

CONTROL = "A0"
_MERGED_LISTS = ("sent_texts", "tools", "guards")


def candidate_turn(row: Mapping[str, Any]) -> Turn:
    """El turno simulado, con su complemento (si hubo) como parte de la respuesta."""
    merged = dict(row)
    complement = row.get("complement")
    if isinstance(complement, Mapping):
        for field in _MERGED_LISTS:
            merged[field] = [*(row.get(field) or []), *(complement.get(field) or [])]
        if complement.get("llm_text"):
            merged["llm_text"] = "\n\n".join(t for t in (row.get("llm_text"), complement.get("llm_text")) if t)
    return turn_from_trace(merged, default_turn=int(row.get("turn") or 1))


def _metadata(bench_dir: Path, sid: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads((bench_dir / "vault" / sid / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def real_trajectory(bench_dir: Path, sid: str, episode_id: str) -> Trajectory:
    episodes = _metadata(bench_dir, sid).get("episodes") or []
    episode = next((e for e in episodes if isinstance(e, dict) and e.get("episode_id") == episode_id), None)
    traces = turn_traces.traces_for_episode(bench_dir / "vault", sid, episode_id)
    return build_trajectory(traces, session_id=sid, episode=episode or {"episode_id": episode_id})


def episode_at(case: Mapping[str, Any]) -> dict[str, Any] | None:
    """El episodio como estaba al inicio del turno del caso. `episodes_at`
    ya viene sin cierre, pero con la orden FINAL del episodio abierto: la del
    momento es la del estado anterior (`state_before`), para que un turno
    anterior a la orden no la vea (revisión de #358)."""
    found = next(
        (e for e in case.get("episodes_at") or [] if isinstance(e, dict) and e.get("episode_id") == case.get("episode_id")),
        None,
    )
    if found is None:
        return None
    state = case.get("state_before")
    return {**found, "order_id": state.get("order_id")} if isinstance(state, Mapping) else dict(found)


async def score_arm(
    bench_dir: Path,
    cases: list[dict[str, Any]],
    *,
    arm: str,
    rep: int,
    rows: Mapping[str, list[dict[str, Any]]] | None,
    ctx: CheckContext,
    judge: Any | None,
) -> list[dict[str, Any]]:
    """Un registro por episodio del banco para el brazo `arm` en la repetición
    `rep`. `rows` = trazas publicadas del brazo por sesión (con la identidad
    del caso real); para A0 no se usan: los candidatos son los turnos reales."""
    by_episode: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_episode[(str(case["session_id"]), str(case["episode_id"]))].append(case)
    records: list[dict[str, Any]] = []
    for (sid, episode_id), ep_cases in sorted(by_episode.items()):
        real = real_trajectory(bench_dir, sid, episode_id)
        wanted = sorted({int(c["turn"]) for c in ep_cases})
        if arm == CONTROL:
            by_turn = {t.turn: t for t in real.turns}
        else:
            by_turn = {
                int(r["turn"]): candidate_turn(r)
                for r in (rows or {}).get(sid, [])
                if r.get("episode_id") == episode_id and isinstance(r.get("turn"), int)
            }
        candidates = {k: by_turn[k] for k in wanted if k in by_turn}
        missing = [k for k in wanted if k not in by_turn]
        episodes_at = {int(c["turn"]): at for c in ep_cases if (at := episode_at(c)) is not None}
        judge_results: dict[int, list] = {}
        if judge is not None and candidates:
            judge_results = await run_judge_checks_focus(real, candidates, ctx, judge, episodes_at=episodes_at)
        judged = [r for results in judge_results.values() for r in results]
        errors = sum(1 for r in judged if is_judge_error(r))
        record = service.score_turns(real, candidates, ctx, episodes_at=episodes_at, judge_results=judge_results)
        last = max(candidates) if candidates else None
        stage_final = (
            focus_trajectory(real, candidates[last], episode_at=episodes_at.get(last)).stage_final if last else None
        )
        record.update(
            {
                "arm": arm,
                "rep": rep,
                "stage_final": stage_final,
                "episode_date": service.episode_date(real),
                "judge": len(judged) > errors,
                "judge_errors": errors,
                "missing_turns": missing,
            }
        )
        records.append(record)
    return records
