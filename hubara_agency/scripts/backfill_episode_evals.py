"""Backfill de calidad por episodio: evalúa TODOS los episodios cerrados sin nota.

Cierra el hueco del muestreo temporal — el cron de 24h (`SalesEvalWorkflow`)
nunca toca episodios viejos, así que los históricos quedan sin calificar. Este
script enumera cada episodio CERRADO del vault que aún no tiene eval en el
histórico y arranca un `EvaluateEpisodeWorkflow` por cada uno: la MISMA eval que
dispara el cierre en vivo (event-driven), aplicada al pasado.

USO (one-shot, desde ops):
    cd hubara_agency && uv run python scripts/backfill_episode_evals.py --dry-run
    cd hubara_agency && uv run python scripts/backfill_episode_evals.py
    cd hubara_agency && uv run python scripts/backfill_episode_evals.py --limit 20

COSTO: cada episodio gasta el juez LLM (~9 métricas). El backfill EXCLUYE los
episodios ya evaluados, así que es re-ejecutable sin duplicar ni gastar de más.
Fire-and-forget: encola los workflows y el worker `sales_eval` los procesa; los
resultados aparecen en la pestaña Calidad LLM a medida que terminan.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.platform.plugin_manifest import get_task_queue  # noqa: E402
from src.platform.temporal.client import get_temporal_client  # noqa: E402
from src.plugins.chats.agent.sales_eval.evals import (  # noqa: E402
    composition,
    history,
    reconstruct,
    select,
)
from src.plugins.chats.agent.sales_eval.evals.contracts import (  # noqa: E402
    EvaluateEpisodeInput,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill: evalúa episodios cerrados sin nota (event-driven, al pasado)."
    )
    parser.add_argument("--dry-run", action="store_true", help="lista sin disparar nada")
    parser.add_argument("--limit", type=int, default=0, help="tope de episodios (0 = todos)")
    parser.add_argument("--min-turns", type=int, default=4, help="mínimo de turnos por episodio")
    args = parser.parse_args()

    vault = composition.get_vault_dir()
    already = history.read_evaluated_session_episodes(composition.get_eval_history_dir())
    units = select.select_all_closed_episodes(
        vault_dir=vault, min_turns=args.min_turns, already=already
    )
    if args.limit > 0:
        units = units[: args.limit]

    print(
        f"episodios cerrados SIN eval: {len(units)}  "
        f"(ya evaluados, excluidos: {len(already)})"
    )
    for u in units:
        print(f"  {u}")

    if args.dry_run:
        print("\n--dry-run: nada disparado.")
        return
    if not units:
        print("nada pendiente — todos los episodios cerrados ya tienen nota.")
        return

    client = await get_temporal_client()
    task_queue = get_task_queue("chats", "sales_eval")
    queued = 0
    for unit in units:
        session_id, episode_id = reconstruct.parse_eval_unit_id(unit)
        try:
            await client.start_workflow(
                "EvaluateEpisodeWorkflow",
                EvaluateEpisodeInput(
                    session_id=session_id, episode_id=episode_id, closing_tag="backfill"
                ),
                id=f"eval-episode-{session_id}-{episode_id}",
                task_queue=task_queue,
            )
            queued += 1
        except Exception as exc:  # noqa: BLE001 — ya-encolado u otro: seguimos
            print(f"  ⚠️  {unit}: {exc.__class__.__name__} (skip)")

    print(
        f"\n✅ {queued} EvaluateEpisodeWorkflow encolados en '{task_queue}'.\n"
        f"   Los resultados aparecen en Calidad LLM a medida que el worker los procesa."
    )


if __name__ == "__main__":
    asyncio.run(main())
