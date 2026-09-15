"""Backfill del scorecard (HU-SC-1) — califica el histórico con checks de código.

Recorre los episodios CERRADOS del vault que todavía no tienen scorecard y los
califica solo con checks de código (sin juez: el backfill es gratis y
determinista). Los episodios anteriores a la traza por turno salen con
fidelidad `legacy`: los checks que dependen de la traza quedan `desconocido`.

Idempotente: lo ya calificado se salta. El script de ops es
`scripts/backfill_scorecards.py`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from src.plugins.chats.agent.sales_eval.evals import select
from src.plugins.chats.agent.sales_eval.evals.reconstruct import parse_eval_unit_id
from src.plugins.chats.agent.sales_eval.scorecard import service, store
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext

_MAX_DAY_FILES = 400
# Episodios reales: `ep_001`. Los `ep-seed-*` son del dataset sintético de prueba.
_REAL_EPISODE_RE = re.compile(r"ep_\d{1,6}")


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


def episodes_active_between(
    vault_dir: Path, *, start_ms: int, end_ms: int, now_ms: int | None = None
) -> list[tuple[str, str]]:
    """Episodios reales cuya vida se solapa con `[start_ms, end_ms)`.

    Abiertos o cerrados: un episodio abierto vive hasta `now_ms`. Solo los que
    tienen mensajes del cliente (los demás son remarketing o notificaciones).
    Orden estable por sesión y episodio. Lo usa `scripts/rescore_scorecards.py`
    para recalificar un rango de fechas con el juez.
    """
    from src.plugins.chats.agent.sales_eval.evals import reconstruct

    now = int(time.time() * 1000) if now_ms is None else now_ms
    out: list[tuple[str, str]] = []
    try:
        sessions = sorted(p.name for p in vault_dir.iterdir() if p.is_dir() and p.name.startswith("wa_"))
    except OSError:
        return []
    for session_id in sessions:
        metadata = reconstruct.read_session_metadata(vault_dir, session_id)
        for ep in metadata.get("episodes") or []:
            if not isinstance(ep, dict) or not _REAL_EPISODE_RE.fullmatch(str(ep.get("episode_id") or "")):
                continue
            started, closed = ep.get("started_at_ms"), ep.get("closed_at_ms")
            if not isinstance(started, (int, float)) or started >= end_ms:
                continue
            if (closed if isinstance(closed, (int, float)) else now) < start_ms:
                continue
            events, _ = reconstruct.read_episode_events(vault_dir, session_id, str(ep["episode_id"]))
            if any(isinstance(e, dict) and e.get("role") == "user" for e in events):
                out.append((session_id, str(ep["episode_id"])))
    return out
