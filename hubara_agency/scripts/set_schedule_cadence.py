"""Cambia la cadencia de un Temporal Schedule EN CALIENTE (sin redeploy).

────────────────────────────────────────────────────────────────────
POR QUÉ ESTE SCRIPT (ACK-3)
────────────────────────────────────────────────────────────────────
Los schedulers cron/interval (orders-reconcile, sales-eval-online,
golden-eval) son **Temporal Schedules**: el cron/intervalo NO vive en el
pod del worker, vive en el server de Temporal. Por eso NO se cambian por
variable de entorno (env → requiere reiniciar el pod) — se cambian
actualizando el Schedule en Temporal, y el cambio aplica al instante en la
próxima ventana de disparo. Cero redeploy, cero restart.

(De hecho los workers sólo CREAN el schedule al boot y lo skipean si ya
existe — un cambio de env + restart no re-tunearía un schedule existente.
Este es el camino correcto para tunearlos en vivo.)

Para los knobs que NO son Temporal Schedules (watchdog timing, umbrales del
evaluate-episode) el camino de hot-reload es otro (AWS SSM / ConfigMap
montado) — ver config/schedulers.yaml y la issue ACK-3.

────────────────────────────────────────────────────────────────────
USO
────────────────────────────────────────────────────────────────────
    # Listar los schedulers tuneables en caliente (del catálogo):
    cd hubara_agency && uv run python scripts/set_schedule_cadence.py --list

    # Cambiar un cron (sales-eval-online → 8pm en vez de 11pm):
    cd hubara_agency && uv run python scripts/set_schedule_cadence.py \\
        sales-eval-online --cron "0 20 * * *"

    # Cambiar un intervalo (orders-reconcile → cada 2 min):
    cd hubara_agency && uv run python scripts/set_schedule_cadence.py \\
        orders-reconcile --every-minutes 2

    # Pausar / reanudar un schedule (sin borrarlo):
    cd hubara_agency && uv run python scripts/set_schedule_cadence.py \\
        golden-eval --pause
    cd hubara_agency && uv run python scripts/set_schedule_cadence.py \\
        golden-eval --unpause

Se referencia el scheduler por su `id` del catálogo (config/schedulers.yaml);
el script resuelve el `schedule_id` real de Temporal desde ahí.

Desde AWS (EKS) se corre como un `kubectl exec` en un pod del worker o como
un Job one-off con la misma imagen — apunta al mismo Temporal del cluster.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import yaml

# Permitir ejecucion directa (`python scripts/...py`).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from temporalio.client import (  # noqa: E402
    Client,
    ScheduleIntervalSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from src.platform.temporal.client import get_temporal_client  # noqa: E402

_CATALOG_PATH = _REPO_ROOT / "config" / "schedulers.yaml"


def _load_temporal_schedulers() -> dict[str, dict]:
    """id del catálogo → entry, sólo los cron/interval con schedule_id."""
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for entry in data.get("schedulers", []):
        if entry.get("cadence_kind") in ("cron", "interval") and entry.get("schedule_id"):
            out[entry["id"]] = entry
    return out


def _print_list(schedulers: dict[str, dict]) -> None:
    print("Schedulers tuneables en caliente (Temporal Schedules):\n")
    for cid, entry in schedulers.items():
        kind = entry["cadence_kind"]
        default = entry.get("default")
        print(f"  {cid}")
        print(f"      schedule_id: {entry['schedule_id']}")
        print(f"      cadence:     {kind} (default: {default})")
        print(f"      activates:   {entry['activates']}")
        print()


async def _apply(
    *,
    schedule_id: str,
    cron: str | None,
    every_minutes: int | None,
    pause: bool,
    unpause: bool,
) -> None:
    client: Client = await get_temporal_client()
    handle = client.get_schedule_handle(schedule_id)

    if pause:
        await handle.pause(note="set_schedule_cadence.py --pause")
        print(f"⏸️  Schedule '{schedule_id}' PAUSADO (no se borra).")
        return
    if unpause:
        await handle.unpause(note="set_schedule_cadence.py --unpause")
        print(f"▶️  Schedule '{schedule_id}' REANUDADO.")
        return

    def _updater(inp: ScheduleUpdateInput) -> ScheduleUpdate:
        schedule = inp.description.schedule
        if cron is not None:
            schedule.spec.cron_expressions = [cron]
            schedule.spec.intervals = []
        if every_minutes is not None:
            schedule.spec.intervals = [
                ScheduleIntervalSpec(every=timedelta(minutes=every_minutes))
            ]
            schedule.spec.cron_expressions = []
        return ScheduleUpdate(schedule=schedule)

    await handle.update(_updater)
    what = f"cron='{cron}'" if cron is not None else f"every={every_minutes}min"
    print(f"✅ Schedule '{schedule_id}' actualizado en caliente → {what}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scheduler_id",
        nargs="?",
        help="id del scheduler en config/schedulers.yaml (ej. sales-eval-online).",
    )
    parser.add_argument("--list", action="store_true", help="Lista los schedulers tuneables y sale.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cron", help="Nueva expresión cron (ej. '0 20 * * *').")
    group.add_argument("--every-minutes", type=int, help="Nuevo intervalo en minutos.")
    group.add_argument("--pause", action="store_true", help="Pausa el schedule.")
    group.add_argument("--unpause", action="store_true", help="Reanuda el schedule.")
    args = parser.parse_args()

    schedulers = _load_temporal_schedulers()

    if args.list or not args.scheduler_id:
        _print_list(schedulers)
        if not args.scheduler_id and not args.list:
            parser.error("Falta el scheduler_id (o usá --list).")
        return

    entry = schedulers.get(args.scheduler_id)
    if entry is None:
        parser.error(
            f"Scheduler {args.scheduler_id!r} no es un Temporal Schedule tuneable. "
            f"Conocidos: {', '.join(schedulers) or '(ninguno)'}"
        )

    if not any([args.cron, args.every_minutes, args.pause, args.unpause]):
        parser.error("Especificá una acción: --cron, --every-minutes, --pause o --unpause.")

    if args.cron is not None and entry["cadence_kind"] != "cron":
        parser.error(f"{args.scheduler_id!r} es de tipo {entry['cadence_kind']}, no acepta --cron.")
    if args.every_minutes is not None and entry["cadence_kind"] != "interval":
        parser.error(f"{args.scheduler_id!r} es de tipo {entry['cadence_kind']}, no acepta --every-minutes.")

    try:
        asyncio.run(
            _apply(
                schedule_id=entry["schedule_id"],
                cron=args.cron,
                every_minutes=args.every_minutes,
                pause=args.pause,
                unpause=args.unpause,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 — ops script: superficie de error legible
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
