"""Crea o actualiza la Temporal Schedule que dispara CatalogSyncWorkflow.

Idempotente: re-correrlo no duplica la Schedule.

Uso:
    uv run python scripts/create_catalog_sync_schedule.py --env <dev|staging|prod>

Validado contra temporalio>=1.25 (ver pyproject.toml). Si Temporal sube la
mayor del SDK, revalidar la firma de `Schedule(action=..., spec=..., policy=...)`.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

# Permitir ejecucion directa (`python scripts/...py`) anadiendo el repo
# root al sys.path. `uv run python -m scripts.create_catalog_sync_schedule`
# tambien funciona sin esto, pero ops typically corre con `python <file>`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)

from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.workflows import CatalogSyncWorkflow
from src.platform.catalog.paths import get_snapshot_dir
from src.platform.constants import CATALOG_SYNC_QUEUE
from src.platform.temporal.client import get_temporal_client

SCHEDULE_ID = "catalog-sync-default"


def _build_schedule(interval_minutes: int, snapshot_dir: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=snapshot_dir,
            ),
            id=f"catalog-sync-{{ScheduledTime}}",
            task_queue=CATALOG_SYNC_QUEUE,
        ),
        spec=ScheduleSpec(
            intervals=[
                ScheduleIntervalSpec(every=timedelta(minutes=interval_minutes))
            ],
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def upsert_schedule(
    *, interval_minutes: int, dry_run: bool
) -> None:
    snapshot_dir = str(get_snapshot_dir())
    schedule = _build_schedule(interval_minutes, snapshot_dir)

    if dry_run:
        print(
            f"[dry-run] Would upsert schedule={SCHEDULE_ID} "
            f"every={interval_minutes}min snapshot_dir={snapshot_dir}"
        )
        return

    client: Client = await get_temporal_client()
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        print(f"Created schedule {SCHEDULE_ID}")
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(lambda _input: schedule)
        print(f"Updated existing schedule {SCHEDULE_ID}")

    # Trigger inmediato (primer sync sin esperar al intervalo).
    handle = client.get_schedule_handle(SCHEDULE_ID)
    await handle.trigger()
    print(f"Triggered immediate run of {SCHEDULE_ID}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "staging", "prod"],
        help="Solo para logging; la config Temporal viene de env vars.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            upsert_schedule(
                interval_minutes=args.interval_minutes,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
