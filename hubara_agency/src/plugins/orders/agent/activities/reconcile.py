"""Activity: reconcile_pending_orders.

Envuelve el barrido idempotente (`reconcile_all_pending`) en una activity
Temporal con heartbeat. La activity es la frontera legítima para leer env
(R-DET) y obtener el port real de la composition (R-STATELESS).
"""
from __future__ import annotations

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.orders.composition import (
    get_order_query_port,
    get_order_registration_port,
)
from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.orders.agent.contracts import ReconcileInput, ReconcileResult
from src.plugins.orders.order_totals_sync import sync_order_totals
from src.plugins.orders.reconcile_runner import reconcile_all_pending


@activity.defn(name="reconcile_pending_orders")
@with_heartbeat(every=10)
async def reconcile_pending_orders_activity(
    input: ReconcileInput,
) -> ReconcileResult:
    """Reintenta TODOS los pedidos pendientes contra Medusa. Idempotente.

    R-DET: la activity (no el workflow) resuelve `vault_dir` desde env si
      llega vacío.
    R-HEARTBEAT: N records × round-trips a Medusa puede superar 10s — el
      `@with_heartbeat` mantiene viva la activity.
    R-STATELESS: el port viene de `get_order_registration_port()` (lru_cache
      en la composition), no de un cache module-level.
    """
    vault_dir = input.vault_dir or str(WORKSPACE_VAULT_DIR)
    port = get_order_registration_port()
    summary = await reconcile_all_pending(
        vault_dir=vault_dir, port=port, max_attempts=input.max_attempts,
    )
    activity.logger.info(
        "reconcile_pending_orders: total=%d resolved=%d still_failing=%d "
        "abandoned=%d errors=%d",
        summary.total, summary.resolved, summary.still_failing,
        summary.abandoned, summary.errors,
    )
    return ReconcileResult(
        total=summary.total,
        resolved=summary.resolved,
        still_failing=summary.still_failing,
        abandoned=summary.abandoned,
        errors=summary.errors,
    )


@activity.defn(name="sync_order_totals")
@with_heartbeat(every=10)
async def sync_order_totals_activity(input: ReconcileInput) -> int:
    """Alinea `episode.order_total_cop` del vault con el total vivo de Medusa.

    Pedido #31 (2026-09-17): una orden editada en Medusa dejaba a Ads con el
    total viejo. Devuelve cuántos chats cambiaron. Idempotente.
    """
    vault_dir = input.vault_dir or str(WORKSPACE_VAULT_DIR)
    summary = await sync_order_totals(
        vault_dir=vault_dir, port=get_order_query_port()
    )
    activity.logger.info(
        "sync_order_totals: wanted=%d found=%d updated=%d",
        summary.wanted_orders, summary.found_orders, summary.updated_sessions,
    )
    return summary.updated_sessions
