"""Backfill del scorecard (HU-SC-1) — califica el histórico con checks de código.

Recorre los episodios CERRADOS del vault que todavía no tienen scorecard y los
califica solo con checks de código (sin juez: el backfill es gratis y
determinista). Los episodios anteriores a la traza por turno salen con
fidelidad `legacy`: los checks que dependen de la traza quedan `desconocido`.

Idempotente: lo ya calificado se salta. El script de ops es
`scripts/backfill_scorecards.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.plugins.chats.agent.sales_eval.evals import select
from src.plugins.chats.agent.sales_eval.evals.reconstruct import parse_eval_unit_id
from src.plugins.chats.agent.sales_eval.scorecard import service, store
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext

_MAX_DAY_FILES = 400


@dataclass(frozen=True)
class BackfillResult:
    scored: int = 0
    skipped_existing: int = 0
    errors: int = 0


def _scored_units(vault_dir: Path) -> set[tuple[str, str]]:
    directory = store.scorecards_dir(vault_dir)
    try:
        dates = [p.stem for p in sorted(directory.glob("*.jsonl"))][-_MAX_DAY_FILES:]
    except OSError:
        return set()
    return {
        (str(r.get("session_id")), str(r.get("episode_id")))
        for r in store.read_scorecards(directory, dates=dates)
    }


def backfill_scorecards(
    vault_dir: Path,
    *,
    ctx: CheckContext,
    dry_run: bool = False,
    limit: int | None = None,
) -> BackfillResult:
    done = _scored_units(vault_dir)
    units = select.select_all_closed_episodes(vault_dir=vault_dir, min_turns=1)
    scored = skipped = errors = 0
    for unit in units:
        session_id, episode_id = parse_eval_unit_id(unit)
        if not episode_id:
            continue  # sesiones sin episodios: fuera del scorecard por episodio
        if (session_id, episode_id) in done:
            skipped += 1
            continue
        if limit is not None and scored >= limit:
            break
        try:
            traj = service.load_trajectory(vault_dir, session_id, episode_id)
            record = service.score_trajectory(traj, ctx)
            if not dry_run:
                store.append_scorecard(store.scorecards_dir(vault_dir), record)
            scored += 1
        except Exception:  # noqa: BLE001 — un episodio roto no frena el backfill
            errors += 1
    return BackfillResult(scored=scored, skipped_existing=skipped, errors=errors)
