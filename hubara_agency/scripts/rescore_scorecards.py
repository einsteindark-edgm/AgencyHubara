"""Recalifica con el scorecard por etapa los episodios con actividad en un rango de fechas.

Arranca `ScoreEpisodeWorkflow` en el worker `sales_eval` por cada episodio real
(abierto o cerrado) que se solapa con el rango, UNO A LA VEZ: es el mismo camino
que "Recalcular con juez" del dashboard, y en serie no satura el límite por
minuto del juez. Idempotente: el id del workflow es estable por episodio y el
registro más nuevo reemplaza al anterior en el dashboard.

Las fechas son días de Bogotá, ambos inclusive.

USO (desde un contenedor con el vault montado y acceso a Temporal, p. ej. el
worker `sales_eval` de prod):
    python scripts/rescore_scorecards.py --since 2026-09-09 --until 2026-09-15 --dry-run
    python scripts/rescore_scorecards.py --since 2026-09-09 --until 2026-09-15
    python scripts/rescore_scorecards.py --since 2026-09-09 --until 2026-09-15 --no-judge
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.plugins.chats.agent.sales_eval.evals import composition  # noqa: E402
from src.plugins.chats.agent.sales_eval.scorecard.backfill import episodes_active_between  # noqa: E402

BOGOTA = timezone(timedelta(hours=-5))
_PAUSE_S = 20


def bogota_range_ms(since: str, until: str) -> tuple[int, int]:
    start = datetime.combine(date.fromisoformat(since), datetime.min.time(), BOGOTA)
    end = datetime.combine(date.fromisoformat(until) + timedelta(days=1), datetime.min.time(), BOGOTA)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="primer día (Bogotá), YYYY-MM-DD")
    ap.add_argument("--until", required=True, help="último día (Bogotá), YYYY-MM-DD, inclusive")
    ap.add_argument("--dry-run", action="store_true", help="lista los episodios sin calificar")
    ap.add_argument("--no-judge", action="store_true", help="solo checks de código")
    args = ap.parse_args()

    start_ms, end_ms = bogota_range_ms(args.since, args.until)
    units = episodes_active_between(composition.get_vault_dir(), start_ms=start_ms, end_ms=end_ms)
    print(f"{len(units)} episodios con actividad entre {args.since} y {args.until} (Bogotá)")
    for session_id, episode_id in units:
        print(f"  …{session_id[-4:]} {episode_id}")
    if args.dry_run or not units:
        return

    from temporalio.exceptions import WorkflowAlreadyStartedError

    from src.plugins.chats.agent.sales_eval.evals.contracts import ScoreEpisodeInput
    from src.sdk import get_task_queue
    from src.sdk.runtime import get_temporal_client

    client = await get_temporal_client()
    task_queue = get_task_queue("chats", "sales_eval")
    for i, (session_id, episode_id) in enumerate(units):
        workflow_id = f"scorecard-{session_id}-{episode_id}"
        try:
            handle = await client.start_workflow(
                "ScoreEpisodeWorkflow",
                ScoreEpisodeInput(session_id=session_id, episode_id=episode_id, with_judge=not args.no_judge),
                id=workflow_id,
                task_queue=task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(workflow_id)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=900)
            verdict = result["verdict"] if isinstance(result, dict) else result.verdict
            print(f"  …{session_id[-4:]} {episode_id}: {verdict}", flush=True)
        except Exception as exc:  # noqa: BLE001 — un episodio no frena el resto
            print(f"  …{session_id[-4:]} {episode_id}: ERROR {exc!r}"[:200], flush=True)
        if i < len(units) - 1:
            await asyncio.sleep(_PAUSE_S)


if __name__ == "__main__":
    asyncio.run(main())
