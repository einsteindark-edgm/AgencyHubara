"""Backfill del scorecard por etapa: califica los episodios cerrados sin scorecard.

Solo checks de código (sin juez): gratis, determinista y re-ejecutable. Los
episodios anteriores a la traza por turno salen con fidelidad `legacy` y sus
checks dependientes de la traza quedan `desconocido`. Los resultados aparecen
en la pestaña Calidad LLM → Conversaciones.

USO (one-shot, desde ops, con el vault montado):
    cd hubara_agency && uv run python scripts/backfill_scorecards.py --dry-run
    cd hubara_agency && uv run python scripts/backfill_scorecards.py --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.plugins.chats.agent.sales_eval.evals import composition  # noqa: E402
from src.plugins.chats.agent.sales_eval.scorecard.backfill import backfill_scorecards  # noqa: E402
from src.plugins.chats.agent.sales_eval.scorecard.catalog_context import (  # noqa: E402
    build_check_context,
)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="califica sin guardar")
    ap.add_argument("--limit", type=int, default=None, help="máximo de episodios a calificar")
    args = ap.parse_args()

    vault = composition.get_vault_dir()
    ctx = await build_check_context()
    print(f"vault: {vault} · catálogo {'disponible' if ctx.catalog_available else 'NO disponible'}")
    result = backfill_scorecards(vault, ctx=ctx, dry_run=args.dry_run, limit=args.limit)
    mode = " (dry-run, nada guardado)" if args.dry_run else ""
    print(
        f"calificados: {result.scored} · ya tenían scorecard: {result.skipped_existing} · "
        f"errores: {result.errors}{mode}"
    )


if __name__ == "__main__":
    asyncio.run(main())
