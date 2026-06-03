"""Reintenta TODOS los pedidos pendientes de reconciliar contra Medusa.

Cierra el loop Premortem F2+K1: cuando Medusa estuvo caído durante un cierre
de venta, el intento quedó en `metadata.failed_order_registrations[]` con
`status="pending"`. Este script barre el vault y reintenta cada uno con el
núcleo idempotente (`reconcile_one`). Los que ahora SÍ entran a Medusa quedan
`resolved` y desaparecen del banner del dashboard; los que siguen fallando se
reintentan en la próxima corrida; los que agotan `max_attempts` quedan
`abandoned` (requieren acción manual del operador).

────────────────────────────────────────────────────────────────────
USO
────────────────────────────────────────────────────────────────────
    # Una corrida manual:
    cd hubara_agency && uv run python scripts/reconcile_pending_orders.py

────────────────────────────────────────────────────────────────────
OPERACIONALIZACIÓN (reintento automático)
────────────────────────────────────────────────────────────────────
Este script es IDEMPOTENTE — correrlo de más es seguro. Programarlo cada
N minutos da el reintento automático "cuando Medusa vuelve":

  * k8s CronJob:        schedule: "*/5 * * * *"  → ejecuta este comando.
  * crontab:            */5 * * * * cd .../hubara_agency && uv run python scripts/reconcile_pending_orders.py
  * Temporal Schedule:  una @activity.defn que importe `reconcile_all_pending`
                        (la lógica ya vive en `plugins/orders/reconcile_runner.py`)
                        disparada por un ScheduleSpec. El núcleo no cambia.

No imprime secretos. El port real lo decide la composition (Medusa si está
configurado, stub si no — en cuyo caso NO marca resuelto, ver reconciliation.py).
"""
from __future__ import annotations

import asyncio

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.logging import setup_logging
from src.platform.orders.composition import get_order_registration_port
from src.platform.orders.reconciliation import (
    OUTCOME_ABANDONED,
    OUTCOME_ERROR,
    OUTCOME_RESOLVED,
)
from src.plugins.orders.reconcile_runner import reconcile_all_pending

setup_logging()


async def main() -> None:
    logger.info("🔁 Reconciliación de pedidos pendientes — inicio")
    port = get_order_registration_port()
    logger.info("OrderRegistrationPort = {}", type(port).__name__)

    summary = await reconcile_all_pending(
        vault_dir=WORKSPACE_VAULT_DIR, port=port
    )

    logger.info(
        "✅ Reconciliación terminada: total={} resolved={} still_failing={} "
        "abandoned={} errors={}",
        summary.total, summary.resolved, summary.still_failing,
        summary.abandoned, summary.errors,
    )
    for o in summary.outcomes:
        if o.outcome == OUTCOME_RESOLVED:
            logger.info(
                "  ✔ {} → {} ({})", o.audit_id, o.resolved_order_id, o.provider
            )
        elif o.outcome == OUTCOME_ABANDONED:
            logger.warning(
                "  ✗ {} ABANDONADO tras {} intentos — requiere acción manual",
                o.audit_id, o.attempts,
            )
        elif o.outcome == OUTCOME_ERROR:
            logger.error("  ⚠ {} ERROR: {}", o.audit_id, o.error_detail)


if __name__ == "__main__":
    asyncio.run(main())
