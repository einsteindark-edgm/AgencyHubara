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

#: Etapa del pedido → evento CAPI post-compra (auditoría 2026-09-08). Las
#: etapas intermedias (preparing / ready) no tienen evento en Meta.
_CAPI_EVENT_BY_STAGE: dict[str, str] = {
    "shipping": "OrderShipped",
    "delivered": "OrderDelivered",
    "cancelled": "OrderCanceled",
}


def capi_event_for_stage(to_stage: str) -> str | None:
    return _CAPI_EVENT_BY_STAGE.get(to_stage)


async def _emit_stage_capi(session_id: str, order_id: str, to_stage: str) -> None:
    """Encola + flushea el evento CAPI de la etapa en el outbox del chat.
    Corre dentro de la activity (durable). Best-effort: nunca bloquea la
    notificación ETA. Dedupe extra contra el cierre humano (cancelación),
    que puede haber encolado ``OrderCanceled`` con el id del draft."""
    import json

    from src.sdk.connectorkit import enqueue_capi_event, flush_capi_outbox

    event_name = capi_event_for_stage(to_stage)
    if event_name is None:
        return
    path = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(metadata, dict):
        return
    known = [
        *(metadata.get("capi_outbox") or []),
        *(metadata.get("capi_events_sent") or []),
    ]
    if any(isinstance(e, dict) and e.get("event_name") == event_name and event_name == "OrderCanceled" for e in known):
        return
    try:
        event_id = enqueue_capi_event(
            metadata,
            event_name=event_name,
            session_id=session_id,
            order_id=order_id,
            source=f"order_stage:{to_stage}",
            now_ms=int(time.time() * 1000),
        )
    except ValueError:
        return
    if event_id is None:
        return
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    await flush_capi_outbox(session_id)


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

    # Normalizar display_id ("#6") → backend id: el handler pasa el order_id
    # CRUDO del path y el "#" rompe httpx (fragment). Cache L-2 primero; en
    # miss, una list() lo puebla (el kanban normalmente ya lo hizo).
    from src.platform.orders import display_id_cache
    from src.platform.orders.composition import get_order_query_port

    normalized = order_id.lstrip("#")
    if normalized.isdigit():
        cached = display_id_cache.get(normalized)
        if cached is None:
            await get_order_query_port().list(limit=50, offset=0)
            cached = display_id_cache.get(normalized)
        if cached:
            order_id = cached
        else:
            activity.logger.warning(
                "emit_order_stage: display_id %s no resuelve a pedido — skip",
                order_id,
            )
            return "unresolved_display_id"

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

    try:
        await _emit_stage_capi(session_id, order_id, to_stage)
    except Exception as exc:  # noqa: BLE001 — atribución best-effort
        activity.logger.warning(
            "emit_order_stage: CAPI %s falló (no bloquea): %s", to_stage, exc
        )

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
