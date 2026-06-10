"""Activity durable de emisión de ``OrderStageChangedEvent`` (L-8b).

Reemplaza el fire-and-forget asyncio del API (L-7: el GC mataba tasks
pendientes; sin retry ante Railway lento; pérdida total si el API crasheaba
entre el 200 y el dispatch). Ahora la emisión corre como workflow Temporal
(``EmitOrderStageWorkflow``) en el worker ``orders/reconcile``: retries con
backoff, visibilidad en la UI :8233, y durabilidad ante crashes del API.

A diferencia del emisor viejo, esta activity NO se traga las excepciones —
fallar es el mecanismo de retry de Temporal. La idempotencia río abajo ya
existe (dedup por stage en el claim del ETA).
"""
from __future__ import annotations

import time

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR


@activity.defn(name="emit_order_stage_activity")
async def emit_order_stage_activity(
    order_id: str, to_stage: str
) -> str:
    """Resuelve la sesión dueña del pedido y despacha el evento. Devuelve
    el resultado del dispatch ("signaled_with_start" / "no_session" / ...).
    """
    # Import lazy: el módulo API define el router FastAPI; lo importamos solo
    # al ejecutar (intra-plugin orders→orders, R-DIP OK).
    from src.plugins.orders.api import _resolve_session_for_order

    from src.platform.orchestration import (
        dispatch_envelope_with_client,
        envelope_for,
    )
    from src.platform.temporal.client import get_temporal_client
    from src.plugins.orders.shared.contracts.events import OrderStageChangedEvent

    session_id = await _resolve_session_for_order(
        backend_order_id=order_id,
        shipping_phone=None,
        vault_dir=WORKSPACE_VAULT_DIR,
    )
    if not session_id:
        activity.logger.info(
            "emit_order_stage: order %s sin sesión WhatsApp — no se notifica",
            order_id,
        )
        return "no_session"

    client = await get_temporal_client()
    await dispatch_envelope_with_client(
        envelope_for(
            OrderStageChangedEvent(
                session_id=session_id,
                order_id=order_id,
                to_stage=to_stage,
                occurred_at_ms=int(time.time() * 1000),
            ),
            source_plugin="orders",
            source_worker="reconcile",
        ),
        client,
    )
    activity.logger.info(
        "emit_order_stage: despachado order=%s stage=%s session=%s",
        order_id, to_stage, session_id,
    )
    return "dispatched"
