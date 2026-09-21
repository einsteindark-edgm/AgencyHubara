"""HTTP API del plugin `orders`.

Expone:
  GET  /api/orders/orders              → lista de órdenes (kanban).
  GET  /api/orders/orders/{id}         → detalle de una orden (inspector).
  GET  /api/orders/orders-health       → sanity check del port + Medusa.
  GET  /api/orders/vault-orders        → órdenes que NO están en Medusa
                                          (failed + stub) para que el
                                          operador las reconcilie manualmente.

Fuente de la verdad: Medusa v2 (`/admin/orders` + `/admin/draft-orders`)
via `OrderQueryPort` (`platform/orders/query_port.py`). Si Medusa no esta
configurado, los endpoints devuelven shape valido pero vacio con un flag
`catalog_available=False` para que el frontend pinte un estado vacio
explicito (no error 500).

Datos faltantes (slots que Medusa no tiene todavia: due_date, agent
assignee, timeline detallado, notas, customer history) se devuelven con un
array `data_completeness_missing[]` y el frontend pinta un marker
"Datos pendientes de integración" sobre esos campos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.customer_scoring.composition import (
    get_customer_scoring_port,
    get_customer_summary_adapter,
    utc_now_ms,
)
from src.platform.medusa.client import MedusaAPIError
from src.platform.medusa.composition import get_medusa_client
from src.platform.orders.command_port import (
    CancelOrderCommand,
    ConfirmPaymentCommand,
    ReversePaymentCommand,
    ScheduleDeliveryCommand,
    SetTestOrderCommand,
    TransitionStageCommand,
)
from src.platform.orders.composition import (
    get_order_command_port,
    get_order_query_port,
    get_order_registration_port,
)
from src.platform.orders.reconciliation import (
    OUTCOME_ERROR,
    OUTCOME_NOT_FOUND,
    mark_resolved_manually,
    reconcile_one,
)
from src.platform.orders.state import STAGE_VALUES
from src.platform.state import FilesystemMetadataStore
from src.plugins.orders.vault_scanner import scan_vault_orders
from src.sdk.dashboardkit import get_dashboard_event_bus
from src.sdk.messagingkit import is_in_service_window
from src.sdk.mediakit import (
    delete_outbound_image,
    is_safe_segment,
    media_url_for,
    persist_outbound_image,
)
from src.sdk.runtime import is_vault_session_id

router = APIRouter()
log = logging.getLogger(__name__)


def _publish_orders_changed(order_id: str | None = None) -> None:
    """F1 (auditoría frontend 2026-06-10): cada mutación del API publica al
    bus del dashboard → el frontend invalida sus queries por evento en vez de
    pollear cada 30s. Las mutaciones que ocurren en los WORKERS (register_order
    del agente Sales) llegan por otro camino: el sampler del vault en
    chats/api/dashboard.py detecta el cambio de metadata y publica él."""
    get_dashboard_event_bus().publish("orders", "changed", id=order_id)


def _require_vault_session_id(session_id: str) -> None:
    """400 ANTES de tocar el vault: el id de la URL arma
    `<vault>/<id>/metadata.json` (`..` es el padre del vault, `.` el vault)."""
    if not is_vault_session_id(session_id):
        raise HTTPException(status_code=400, detail="session_id inválido")


@router.get("/orders")
async def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_drafts: bool = Query(
        default=True,
        description=(
            "Si True, fusiona Draft Orders (pedidos recien cerrados via "
            "register_order, pero no completados) con Orders. Default True."
        ),
    ),
) -> dict[str, Any]:
    """Lista de ordenes para el kanban del dashboard.

    Response shape:
      {
        "orders": [OrderSummaryDTO, ...],
        "count": int,
        "offset": int,
        "limit": int,
        "catalog_available": bool,
        "error_detail": str | null
      }

    `catalog_available=false` significa que Medusa no esta configurado /
    fallo — los campos `orders=[]` quedan vacios. El frontend muestra un
    estado vacio en lugar de error.
    """
    port = get_order_query_port()
    result = await port.list(
        limit=limit, offset=offset, include_drafts=include_drafts
    )
    return {
        "orders": [asdict(o) for o in result.orders],
        "count": result.count,
        "offset": result.offset,
        "limit": result.limit,
        "catalog_available": result.catalog_available,
        "error_detail": result.error_detail,
    }


@router.get("/orders/by-session/{session_id}")
async def list_orders_by_session(
    session_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """Pedidos de UN cliente/sesión de WhatsApp — para el panel móvil del chat.

    Medusa NO filtra órdenes por cliente/teléfono, así que el índice
    sesión→órdenes vive en el vault: `metadata.episodes[].order_id` (+ legacy
    `registered_order.order_id`). Extraemos esos ids y pedimos el detalle a
    Medusa por cada uno; devolvemos su `summary` (id, estado, total, fecha).

    Shape: `{"orders": [OrderSummaryDTO, ...], "count": int, "error_detail":
    str|null}`. Un id que existe en el vault pero no en Medusa (`port.get→None`)
    se omite. Sesión sin órdenes → lista vacía (no 404).

    PM2-B4/B5 (premortem 2026-07-14): los `port.get` van EN PARALELO
    (`asyncio.gather`) — en serie, con Medusa@Railway a 2-30s por GET, un
    cliente con 3+ pedidos dejaba el panel colgado y reventaba el timeout del
    cast. Un `MedusaAPIError` en UN id no tumba el endpoint: se omite ese id y
    se reporta en `error_detail` (los demás pedidos SÍ llegan al operador).
    Cap de 15 ids (los más recientes) — `episodes[]` no tiene tope.
    """
    _require_vault_session_id(session_id)

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = metadata_store.read(session_id)
    order_ids = _collect_order_ids_from_metadata(metadata)[-15:]

    port = get_order_query_port()
    results = await asyncio.gather(
        *(port.get(oid) for oid in order_ids), return_exceptions=True
    )
    orders: list[dict[str, Any]] = []
    error_detail: str | None = None
    for oid, res in zip(order_ids, results):
        if isinstance(res, MedusaAPIError):
            log.warning(
                "by-session: Medusa falló para %s (%s) — se omite", oid, res
            )
            error_detail = "Algunos pedidos no se pudieron cargar desde Medusa."
            continue
        if isinstance(res, BaseException):
            raise res
        if res is not None:
            orders.append(asdict(res.summary))
    return {"orders": orders, "count": len(orders), "error_detail": error_detail}


@router.get("/orders/{order_id}")
async def get_order_detail(
    order_id: str = Path(
        ...,
        description=(
            "ID de la orden o draft_order de Medusa (formato "
            "'order_01HXX...' o 'draft_01HXX...'). El endpoint hace "
            "fallback automatico entre ambos si el prefix no matchea."
        ),
        min_length=1,
        max_length=200,
    ),
) -> dict[str, Any]:
    """Detalle de una orden para el panel inspector.

    Response shape: el OrderDetailDTO serializado (incluye summary,
    items_detail, addresses, totales, timeline minimo, payment_method_label,
    y `data_completeness_missing[]` con los slots que la UI debe marcar
    como "Datos pendientes de integración").

    Returns 404 si Medusa no encuentra la orden por id (probado tanto
    `/admin/orders/{id}` como `/admin/draft-orders/{id}`).
    """
    port = get_order_query_port()
    detail = await port.get(order_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Order {order_id!r} not found in Medusa.",
        )
    return asdict(detail)


@router.get("/vault-orders")
async def get_vault_orders() -> dict[str, Any]:
    """Premortem F2+K1: lista pedidos que existen en el vault local pero NO
    en Medusa. Dos casos:

    1. `kind="failed"`: el agente Sales llamó `register_order` pero Medusa
       rechazó (5xx, network down, config rota). El payload completo
       quedó en `metadata.failed_order_registrations[]` para que el
       operador lo registre manualmente.
    2. `kind="stub"`: el agente Sales registró el pedido con
       `StubOrderRegistration` (porque Medusa no estaba configurado al
       momento del cierre). El cliente recibió confirmación pero NO existe
       en Medusa hasta que alguien lo migre.

    Sin este endpoint, esos pedidos serían INVISIBLES en el dashboard —
    el operador podria perder ventas silenciosamente.

    Response shape:
      {
        "records": [
          {
            "kind": "failed" | "stub",
            "session_key": "wa_57311...",
            "order_id": "AUDIT-..." | "HUB-...",
            "customer_phone": "+57...",
            "customer_city": "Bogotá",
            "total_cop": 17000,
            "currency": "COP",
            "items_count": 2,
            "payment_method": "transfer",
            "error_detail": "medusa_api_error: HTTP 503" | null,
            "registered_at_ms": 1779800400000,
            "raw": {...}
          }
        ],
        "count": N,
        "failed_count": N1,
        "stub_count": N2
      }
    """
    records = scan_vault_orders(WORKSPACE_VAULT_DIR)
    failed = [r for r in records if r.kind == "failed"]
    stub = [r for r in records if r.kind == "stub"]
    return {
        "records": [asdict(r) for r in records],
        "count": len(records),
        "failed_count": len(failed),
        "stub_count": len(stub),
    }


@router.post("/vault-orders/{session_key}/{audit_id}/retry")
async def retry_vault_order(
    session_key: str = Path(..., min_length=1, max_length=200),
    audit_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """Reintenta registrar en Medusa UN pedido pendiente. Idempotente.

    Lo dispara el operador desde el banner del dashboard ("Reintentar"). Usa
    el MISMO núcleo idempotente (`reconcile_one`) que el barrido automático
    (`scripts/reconcile_pending_orders.py`), así que apretarlo varias veces
    es seguro: si ya está resuelto devuelve `already_resolved` sin duplicar,
    y el adapter Medusa pre-chequea por fingerprint antes de crear.

    Devuelve el `ReconciliationOutcome` serializado:
      {"session_key", "audit_id", "outcome", "resolved_order_id",
       "provider", "error_detail", "attempts"}.
    `outcome` ∈ resolved | still_failing | already_resolved | abandoned.
    404 si no existe el (session_key, audit_id); 422 si el record está
    malformado y no se puede reconstruir.
    """
    _require_vault_session_id(session_key)
    port = get_order_registration_port()
    outcome = await reconcile_one(
        vault_dir=WORKSPACE_VAULT_DIR,
        session_key=session_key,
        audit_id=audit_id,
        port=port,
    )
    if outcome.outcome == OUTCOME_NOT_FOUND:
        raise HTTPException(
            status_code=404,
            detail=f"Pedido {audit_id!r} no encontrado en sesión {session_key!r}.",
        )
    if outcome.outcome == OUTCOME_ERROR:
        raise HTTPException(
            status_code=422,
            detail=outcome.error_detail or "record de pedido malformado",
        )
    _publish_orders_changed(audit_id)
    return asdict(outcome)


@router.post("/vault-orders/{session_key}/{audit_id}/resolve")
async def resolve_vault_order(
    session_key: str = Path(..., min_length=1, max_length=200),
    audit_id: str = Path(..., min_length=1, max_length=200),
    note: str | None = Body(default=None, embed=True),
    resolved_order_id: str | None = Body(default=None, embed=True),
) -> dict[str, Any]:
    """Marca un pedido pendiente como resuelto a mano.

    Para cuando el operador ya lo registró en Medusa Admin manualmente: saca
    el record del banner SIN tocar Medusa. Idempotente (si ya estaba resuelto,
    devuelve `already_resolved`).

    Body opcional: `{"note": "...", "resolved_order_id": "order_01..."}`.
    404 si no existe el (session_key, audit_id).
    """
    _require_vault_session_id(session_key)
    outcome = mark_resolved_manually(
        vault_dir=WORKSPACE_VAULT_DIR,
        session_key=session_key,
        audit_id=audit_id,
        note=note,
        resolved_order_id=resolved_order_id,
    )
    if outcome.outcome == OUTCOME_NOT_FOUND:
        raise HTTPException(
            status_code=404,
            detail=f"Pedido {audit_id!r} no encontrado en sesión {session_key!r}.",
        )
    _publish_orders_changed(audit_id)
    return asdict(outcome)


@router.patch("/orders/{order_id}/schedule")
async def schedule_order(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(
        ...,
        examples=[
            {
                "delivery_iso": "2026-05-26",
                "delivery_time": "09:00",
                "note": "Cliente prefiere antes de las 10am",
            }
        ],
    ),
) -> dict[str, Any]:
    """Agendar entrega + transicionar `new → preparing`.

    Body:
      * `delivery_iso` (required) — YYYY-MM-DD
      * `delivery_time` (optional) — HH:MM
      * `note` (optional) — texto del humano

    Response: `{success, current_stage, error_detail?}` plano.
      * HTTP 200 con success=True → frontend invalidate query list+detail.
      * HTTP 200 con success=False → frontend muestra el error_detail.
      * HTTP 4xx solo si validación del body falla.
    """
    delivery_iso = body.get("delivery_iso")
    if not isinstance(delivery_iso, str) or not delivery_iso:
        raise HTTPException(
            status_code=422,
            detail="`delivery_iso` (YYYY-MM-DD) es requerido",
        )
    cmd = ScheduleDeliveryCommand(
        order_id=order_id,
        delivery_iso=delivery_iso,
        delivery_time=body.get("delivery_time")
        if isinstance(body.get("delivery_time"), str)
        else None,
        note=body.get("note")
        if isinstance(body.get("note"), str)
        else None,
    )
    port = get_order_command_port()
    result = await port.schedule_delivery(cmd)
    # new → preparing: arranca el seguimiento del Agente ETA (primera notificación).
    if result.success and result.current_stage:
        _spawn_emit(order_id, result.current_stage)
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/stage")
async def transition_order_stage(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(
        ...,
        examples=[{"stage": "ready", "note": "Empaquetado"}],
    ),
) -> dict[str, Any]:
    """Drag-and-drop o click directo. Valida transición permitida.

    Body:
      * `stage` (required) — uno de: new, preparing, ready, shipping,
        delivered, cancelled.
      * `note` (optional)
      * `force` (optional bool, default False) — bypassa DAG. Usar solo
        para correcciones explícitas (UI debe pedir confirm dialog).
      * `by` (optional, default "human") — atribución en el stage history.
      * `notify_customer` (optional bool, default True) — con `false` NO le
        llega WhatsApp al cliente, pero el evento CAPI de la etapa SÍ sale
        (Meta debe saber que se entregó). Lo usan: el agente order-sentinel
        (transición inferida de un chat donde el humano YA avisó) y el
        operador que corrige un pedido ya entregado saltándose etapas
        (`force` + `notify_customer=false` desde el kanban). NO gatear por
        tag HUMANO (L-6: toda venta exitosa termina en HUMANO — apagaría todo).
      * `tracking_url` (optional str) — link de la guía de la transportadora
        que el operador adjunta al mover el pedido a `shipping` (modal del
        kanban). Viaja en la cascada ETA hasta el mensaje de WhatsApp (link
        tappable al final) y queda en la nota del stage history. Solo http(s),
        sin espacios, ≤ 500 chars → si no, 422 ANTES de aplicar la transición
        (un "en camino" con link roto no se puede re-notificar por template).

    Response shape igual que `/schedule`. `success=False` con
    `error_detail` que empieza con `invalid_transition:` cuando el
    movimiento no es permitido (frontend muestra dialog explicativo).
    """
    stage_raw = body.get("stage")
    if stage_raw not in STAGE_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"`stage` debe ser uno de {sorted(STAGE_VALUES)}, "
                f"recibido {stage_raw!r}"
            ),
        )
    tracking_url = _parse_tracking_url(body.get("tracking_url"))
    note = body.get("note") if isinstance(body.get("note"), str) else None
    if tracking_url:
        # Auditoría en el stage history: qué guía se le mandó al cliente.
        guia = f"Guía de envío: {tracking_url}"
        note = f"{note} · {guia}" if note else guia
    cmd = TransitionStageCommand(
        order_id=order_id,
        to_stage=stage_raw,  # type: ignore[arg-type]
        note=note,
        force=bool(body.get("force", False)),
        by=body.get("by") if isinstance(body.get("by"), str) else "human",
    )
    port = get_order_command_port()
    result = await port.transition_stage(cmd)
    # Cada transición de stage gatilla una notificación del Agente ETA. El
    # dispatcher matchea por `to_stage`; stages sin transición declarada (ej.
    # `new`) caen en no-match → no-op. Dedup vive en el workflow ETA.
    # `notify_customer=false` emite igual (CAPI) pero sin WhatsApp al cliente.
    if result.success and result.current_stage:
        _spawn_emit(
            order_id,
            result.current_stage,
            tracking_url,
            notify_customer=body.get("notify_customer", True) is not False,
        )
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/confirm-payment")
async def confirm_order_payment(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Marcar pago como confirmado (manual del humano).

    Body opcional `{"by": "string"}` para auditoría.

    Hoy esto NO toca el `payment_status` real de Medusa (sin gateway
    integrado). Solo escribe `hubara_payment_confirmed=True` en metadata.
    Cuando integremos gateway, este endpoint también capturará el pago en
    Medusa.

    Idempotente: si ya estaba confirmado, devuelve success=True sin
    side effects.
    """
    by = body.get("by") if isinstance(body.get("by"), str) else "human"
    cmd = ConfirmPaymentCommand(order_id=order_id, by=by)
    port = get_order_command_port()
    result = await port.confirm_payment(cmd)
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/reverse-payment")
async def reverse_order_payment(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Reversar un pago confirmado por error operativo.

    Body opcional `{"reason": "texto", "by": "string"}`. El pedido vuelve a
    NO pagado: Medusa registra el refund (si seguía capturado), el flag
    `hubara_payment_confirmed` se apaga y el chat vuelve a "pago pendiente
    de verificar". `invalid_state` si no hay pago que reversar.
    """
    raw_reason = body.get("reason")
    reason = (
        raw_reason.strip()
        if isinstance(raw_reason, str) and raw_reason.strip()
        else None
    )
    by = body.get("by") if isinstance(body.get("by"), str) else "human"
    cmd = ReversePaymentCommand(order_id=order_id, reason=reason, by=by)
    port = get_order_command_port()
    result = await port.reverse_payment(cmd)
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


@router.post("/orders/{order_id}/cancel")
async def cancel_order_endpoint(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Cancelar la orden. Transición a `cancelled` con `force=True`.

    Body opcional `{"reason": "texto"}` — persiste como
    `hubara_cancelled_reason` y aparece en el inspector + timeline.

    Idempotente si ya estaba cancelada (devuelve success=True).
    """
    reason = (
        body.get("reason")
        if isinstance(body.get("reason"), str)
        else None
    )
    cmd = CancelOrderCommand(order_id=order_id, reason=reason)
    port = get_order_command_port()
    result = await port.cancel_order(cmd)
    # Cancelación: el Agente ETA avisa al cliente (si la sesión sigue en ruta eta).
    if result.success and result.current_stage:
        _spawn_emit(order_id, result.current_stage)
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


@router.patch("/orders/{order_id}/test-order")
async def set_test_order_endpoint(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Marcar / desmarcar el pedido como "prueba".

    Body `{"is_test": true|false, "by": "string"}`. `is_test` es obligatorio
    y booleano (422 si no): nunca se cambia la marca por omisión. La marca
    vive en Medusa (`hubara_test_order`); el pedido sigue visible pero sale
    de todos los totales (Orders, Ads, campañas) y no manda eventos a Meta.
    """
    is_test = body.get("is_test")
    if not isinstance(is_test, bool):
        raise HTTPException(status_code=422, detail="is_test debe ser true o false")
    by = body.get("by") if isinstance(body.get("by"), str) else "human"
    cmd = SetTestOrderCommand(order_id=order_id, is_test=is_test, by=by)
    port = get_order_command_port()
    result = await port.set_test_order(cmd)
    if result.success:
        _publish_orders_changed(order_id)
    return _serialize_command_result(result)


def _serialize_command_result(result) -> dict[str, Any]:
    """Shape común para los 4 endpoints write-side."""
    return {
        "success": result.success,
        "order_id": result.order_id,
        "current_stage": result.current_stage,
        "error_detail": result.error_detail,
        "audit_id": result.audit_id,
    }


# Referencias fuertes a las tasks fire-and-forget del emit (L-7): sin esto,
# asyncio puede GC-recolectar una task PENDIENTE mientras espera I/O largo
# (Medusa@Railway tarda 30s+ por GET) — muere sin log, sin warning, y la
# notificación ETA simplemente no sale. Patrón estándar de los docs de
# asyncio: set global + done_callback(discard).
_emit_tasks: set[asyncio.Task] = set()


_TRACKING_URL_MAX_LEN = 500
_TRACKING_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _parse_tracking_url(raw: Any) -> str | None:
    """Normaliza el `tracking_url` del body. Ausente / vacío → None.

    Presente pero inválido → 422 (esquema distinto de http(s), espacios,
    demasiado largo, no-string). Se valida ANTES de tocar el pedido para no
    dejar una transición aplicada sin su link.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=422, detail="`tracking_url` debe ser un string http(s)."
        )
    url = raw.strip()
    if not url:
        return None
    if len(url) > _TRACKING_URL_MAX_LEN or not _TRACKING_URL_RE.match(url):
        raise HTTPException(
            status_code=422,
            detail=(
                "`tracking_url` inválido: debe empezar con http:// o https://, "
                f"no tener espacios y medir ≤ {_TRACKING_URL_MAX_LEN} caracteres."
            ),
        )
    return url


async def _start_durable_emit(
    order_id: str,
    to_stage: str,
    tracking_url: str | None = None,
    notify_customer: bool = True,
) -> None:
    """L-8b: la emisión es un workflow Temporal (EmitOrderStageWorkflow en
    queue-orders-reconcile) — durable, con retries y visible en la UI :8233.
    Reemplaza el create_task best-effort (L-7). El start tarda ~ms; si el
    MISMO (order, stage) ya está en vuelo, Temporal lo dedupea por id.
    `tracking_url` (solo "en camino") viaja en el input del workflow."""
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from src.platform.plugin_manifest import get_task_queue
    from src.platform.temporal.client import get_temporal_client

    try:
        client = await get_temporal_client()
        await client.start_workflow(
            "EmitOrderStageWorkflow",
            {
                "order_id": order_id,
                "to_stage": to_stage,
                "tracking_url": tracking_url,
                "notify_customer": notify_customer,
            },
            id=f"order-stage-changed-{order_id.lstrip('#')}-{to_stage}",
            task_queue=get_task_queue("orders", "reconcile"),
        )
    except WorkflowAlreadyStartedError:
        log.info("eta_emit: emisión ya en vuelo order=%s stage=%s", order_id, to_stage)
    except Exception:  # noqa: BLE001 — la transición YA se aplicó; no romper el 200
        log.warning(
            "eta_emit: no pude ARRANCAR la emisión durable order=%s stage=%s",
            order_id, to_stage, exc_info=True,
        )


def _spawn_emit(
    order_id: str,
    to_stage: str,
    tracking_url: str | None = None,
    notify_customer: bool = True,
) -> None:
    task = asyncio.create_task(
        _start_durable_emit(order_id, to_stage, tracking_url, notify_customer)
    )
    _emit_tasks.add(task)
    task.add_done_callback(_emit_tasks.discard)


# ── Foto del pedido (panel derecho de Órdenes → la manda el ETA) ─────────
#
# La foto vive en la conversación del cliente — ``<vault>/<session>/media/`` +
# ``metadata.order_photos[<order_id backend>]`` — porque ahí la lee el ETA (otro
# contenedor, mismo vault) al pasar el pedido a "listo". Se guardan los BYTES:
# el media_id de Meta vence y el ETA lo pide al enviar.

#: Límite de Meta para imágenes (encabezado de plantilla incluido).
_ORDER_PHOTO_MAX_BYTES = 5 * 1024 * 1024
#: Tipos que WhatsApp acepta como encabezado IMAGE → firma de sus bytes.
_ORDER_PHOTO_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def _order_owner(order_id: str) -> tuple[str, str | None]:
    """``(id backend, sesión WhatsApp | None)`` del pedido (acepta ``#31``). 404
    si Medusa no lo conoce. Misma resolución de sesión que el emisor ETA."""
    detail = await get_order_query_port().get(order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Pedido {order_id!r} no encontrado.")
    phone = None
    if detail.shipping_address is not None:
        phone = detail.shipping_address.phone
    session_id = await _resolve_session_for_order(
        backend_order_id=detail.summary.id,
        shipping_phone=phone or detail.summary.phone,
        vault_dir=WORKSPACE_VAULT_DIR,
    )
    return detail.summary.id, session_id


def _session_metadata(session_id: str) -> dict[str, Any]:
    try:
        data = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_id)
    except Exception:  # noqa: BLE001 — sesión sin metadata = sin foto
        return {}
    return data if isinstance(data, dict) else {}


def _photo_in(data: dict[str, Any], backend_id: str) -> dict[str, Any] | None:
    photos = data.get("order_photos")
    entry = photos.get(backend_id) if isinstance(photos, dict) else None
    return entry if isinstance(entry, dict) and entry.get("filename") else None


def _order_photo_entry(session_id: str, backend_id: str) -> dict[str, Any] | None:
    return _photo_in(_session_metadata(session_id), backend_id)


def _order_photo_payload(backend_id: str, session_id: str | None) -> dict[str, Any]:
    data = _session_metadata(session_id) if session_id else {}
    entry = _photo_in(data, backend_id) if session_id else None
    photo = None
    if entry is not None:
        photo = {
            # `?v=` rompe la caché del <img> al reemplazar la foto.
            "file_url": (
                f"/api/orders/order-photos/{session_id}/{backend_id}"
                f"?v={entry.get('uploaded_at_ms', 0)}"
            ),
            "uploaded_at_ms": entry.get("uploaded_at_ms"),
            "sent_at_ms": entry.get("sent_at_ms"),
            # Lo deja el ETA si Meta rechazó el último envío (p.ej. plantilla
            # sin aprobar): el envío corre lejos del clic del operador.
            "last_error": entry.get("last_send_error"),
        }
    return {
        "order_id": backend_id,
        "photo": photo,
        "has_conversation": session_id is not None,
        # Ventana 24h abierta → el ETA manda la foto como mensaje normal;
        # cerrada (o sin dato) → plantilla. El modal de "Listo" lo muestra.
        "service_window_open": (
            is_in_service_window(int(time.time() * 1000), data) if session_id else None
        ),
    }


def _require_conversation(session_id: str | None) -> str:
    if session_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "El pedido no está ligado a una conversación de WhatsApp: no hay "
                "a quién mandarle la foto."
            ),
        )
    return session_id


@router.get("/orders/{order_id}/photo")
async def get_order_photo(
    order_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """Foto del pedido listo que subió el operador (o ``photo: null``)."""
    backend_id, session_id = await _order_owner(order_id)
    return _order_photo_payload(backend_id, session_id)


@router.put("/orders/{order_id}/photo")
async def upload_order_photo(
    order_id: str = Path(..., min_length=1, max_length=200),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Sube (o reemplaza) la foto del pedido. JPEG/PNG ≤ 5 MB — lo que WhatsApp
    acepta como encabezado de plantilla. Al pasar el pedido a "listo" el ETA la
    manda al cliente con ``order_ready_photo_utility_v1``."""
    backend_id, session_id = await _order_owner(order_id)
    session_id = _require_conversation(session_id)

    mime = (file.content_type or "").split(";")[0].strip().lower()
    signature = _ORDER_PHOTO_SIGNATURES.get(mime)
    if signature is None:
        raise HTTPException(status_code=415, detail="La foto tiene que ser JPEG o PNG.")
    if file.size is not None and file.size > _ORDER_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="La foto pesa más de 5 MB.")
    content = await file.read()
    if len(content) > _ORDER_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="La foto pesa más de 5 MB.")
    if not content.startswith(signature):
        raise HTTPException(status_code=415, detail="Los bytes no son una foto JPEG/PNG válida.")

    token = f"order-{backend_id}-{uuid.uuid4().hex[:12]}"
    filename = persist_outbound_image(session_id, content, mime, token=token)
    now_ms = int(time.time() * 1000)
    replaced: list[str] = []

    def _apply(fresh: dict) -> dict | None:
        if not fresh:
            return None  # metadata ilegible: no pisar la sesión
        photos = fresh.setdefault("order_photos", {})
        old = photos.get(backend_id)
        if isinstance(old, dict) and isinstance(old.get("filename"), str):
            replaced.append(old["filename"])
        photos[backend_id] = {
            "filename": filename,
            "media_ref": media_url_for(session_id, filename),
            "mime": mime,
            "uploaded_at_ms": now_ms,
        }
        return fresh

    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(session_id, _apply)
    if _order_photo_entry(session_id, backend_id) is None:
        delete_outbound_image(session_id, filename)
        raise HTTPException(status_code=409, detail="No se pudo guardar la foto; reintenta.")
    for old_filename in replaced:
        if old_filename != filename:
            delete_outbound_image(session_id, old_filename)
    _publish_orders_changed(backend_id)
    log.info("order_photo: foto subida order=%s session=%s", backend_id, session_id)
    return _order_photo_payload(backend_id, session_id)


@router.delete("/orders/{order_id}/photo")
async def delete_order_photo(
    order_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    backend_id, session_id = await _order_owner(order_id)
    if session_id is None:
        return _order_photo_payload(backend_id, None)
    removed: list[str] = []

    def _apply(fresh: dict) -> dict | None:
        photos = fresh.get("order_photos") if fresh else None
        if not isinstance(photos, dict) or backend_id not in photos:
            return None
        old = photos.pop(backend_id)
        if isinstance(old, dict) and isinstance(old.get("filename"), str):
            removed.append(old["filename"])
        return fresh

    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(session_id, _apply)
    for filename in removed:
        delete_outbound_image(session_id, filename)
    _publish_orders_changed(backend_id)
    return _order_photo_payload(backend_id, session_id)


@router.get("/order-photos/{session_id}/{backend_id}")
async def get_order_photo_file(session_id: str, backend_id: str) -> FileResponse:
    """Sirve la foto al panel (``<img>`` con el token por query, como la media
    del chat). Keyed por sesión: no consulta Medusa en cada carga."""
    if not (is_safe_segment(session_id) and is_safe_segment(backend_id)):
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    entry = _order_photo_entry(session_id, backend_id)
    filename = entry.get("filename") if entry else None
    if not isinstance(filename, str) or not is_safe_segment(filename):
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    path = WORKSPACE_VAULT_DIR / session_id / "media" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    return FileResponse(path, media_type=entry.get("mime") or "image/jpeg")


@router.post("/orders/{order_id}/photo/send", status_code=202)
async def send_order_photo(
    order_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Botón "Enviar ahora": el ETA manda la foto al cliente ya, sin esperar a
    que el pedido pase a "listo" (o la reenvía). ``request_id`` (opcional)
    hace idempotente el doble clic."""
    backend_id, session_id = await _order_owner(order_id)
    session_id = _require_conversation(session_id)
    if _order_photo_entry(session_id, backend_id) is None:
        raise HTTPException(
            status_code=409, detail="El pedido no tiene foto: súbela antes de enviarla."
        )
    raw = (body or {}).get("request_id")
    request_id = raw if isinstance(raw, str) and _REQUEST_ID_RE.match(raw) else uuid.uuid4().hex
    await _start_ready_photo_request(backend_id, session_id, request_id)
    return {"queued": True, "order_id": backend_id}


async def _start_ready_photo_request(order_id: str, session_id: str, request_id: str) -> None:
    """Arranca ``EmitReadyPhotoRequestWorkflow`` (durable, L-8b). Se espera el
    start: si Temporal no responde el operador lo ve (503) en vez de un
    "enviado" falso. Mismo ``request_id`` → mismo id → no duplica."""
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from src.platform.plugin_manifest import get_task_queue
    from src.platform.temporal.client import get_temporal_client

    try:
        client = await get_temporal_client()
        await client.start_workflow(
            "EmitReadyPhotoRequestWorkflow",
            {"order_id": order_id, "session_id": session_id},
            id=f"order-ready-photo-{order_id}-{request_id}",
            task_queue=get_task_queue("orders", "reconcile"),
        )
    except WorkflowAlreadyStartedError:
        log.info("order_photo: envío ya en vuelo order=%s request=%s", order_id, request_id)
    except Exception as exc:  # noqa: BLE001 — el operador tiene que enterarse
        log.warning("order_photo: no pude pedir el envío order=%s", order_id, exc_info=True)
        raise HTTPException(
            status_code=503, detail="No se pudo pedir el envío de la foto; reintenta."
        ) from exc


@router.get("/orders/{order_id}/customer-score")
async def get_customer_score(
    order_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """Compute scoring del cliente asociado al order.

    Flow:
      1. Resolver display_id ("#2") → backend_id (premortem A1 espejado).
      2. Get order detail → extraer phone del shipping_address.
      3. Build session_id = `wa_<phone>` (convención sales worker).
      4. Read vault metadata.json → episodios.
      5. Para cada `episode.order_id`, fetch total + created_at de Medusa
         (paralelizado con asyncio.gather; tolerante a 404 individuales).
      6. Llamar `CustomerScoringPort.score_session(...)`.

    Response shape:
      {
        "tag": "VIP" | "Recurrente" | "Nuevo" | "Frío" | "Estándar" | "Sin datos",
        "score_letter": "A" | "B" | "C" | "D" | "—",
        "score_value": int,
        "score_reason": str,
        "monetary_cop": int,
        "last_purchase_at_ms": int | null,
        "last_purchase_iso": str | null,         # ISO YYYY-MM-DD (UI helper)
        "rules_version": int,
        "breakdown": [{ "feature": str, "feature_value": float, "points": int }, ...],
        "session_id": str | null,                # debug — para que el operador
                                                  # sepa de qué wa_* salió
      }

    Si la order NO tiene phone o el vault no tiene esa sesión, devuelve un
    score "Sin datos" en lugar de 404 (el frontend pinta MissingData).
    """
    qport = get_order_query_port()
    detail = await qport.get(order_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Order {order_id!r} not found in Medusa.",
        )

    # Shipping phone — solo se usa como ÚLTIMO recurso en la resolución
    # (ver _resolve_session_for_order). El link canónico es
    # order.metadata.session_key, no el número de envío.
    shipping_phone: str | None = None
    if detail.shipping_address is not None:
        shipping_phone = detail.shipping_address.phone
    if not shipping_phone:
        shipping_phone = detail.summary.phone

    session_id = await _resolve_session_for_order(
        backend_order_id=detail.summary.id,
        shipping_phone=shipping_phone,
        vault_dir=WORKSPACE_VAULT_DIR,
    )
    if session_id is None:
        # La orden no se puede linkear a ninguna sesión del vault → sin
        # historial (frontend muestra "Sin historial").
        return _empty_score_response()

    # Read vault metadata para extraer order_ids de episodios.
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = metadata_store.read(session_id)
    order_ids_in_episodes = _collect_order_ids_from_metadata(metadata)

    # Fetch totals + created_at de Medusa en paralelo (cap: solo los ids del
    # vault; el customer típicamente tiene <10 orders).
    order_facts = await _customer_order_facts(order_ids_in_episodes)

    # Score.
    score_port = get_customer_scoring_port()
    score = score_port.score_session(
        session_id,
        now_ms=utc_now_ms(),
        order_facts=order_facts,
    )

    # Serialize + enrich con ISO date para el UI.
    payload = asdict(score)
    payload["session_id"] = session_id
    payload["last_purchase_iso"] = (
        _ms_to_iso_date(score.last_purchase_at_ms)
        if score.last_purchase_at_ms
        else None
    )
    return payload


def _phone_match_key(value: str) -> str:
    """Normaliza un phone o session_id a su clave de match: los últimos 10
    dígitos.

    Por qué 10: en Colombia (y la mayoría de LatAm) el móvil nacional son 10
    dígitos (`3125671604`). El número internacional agrega el country code
    (`57` → `573125671604`). Dos identificadores refieren al mismo cliente si
    comparten los últimos 10 dígitos, sin importar:
      * presencia/ausencia del country code (`57`)
      * prefijo `+`
      * separadores (espacios, guiones, paréntesis)

    Si el valor tiene menos de 10 dígitos, devolvemos todos los que haya
    (caller decide si confía en un match tan corto).
    """
    digits = re.sub(r"\D", "", value)
    return digits[-10:] if len(digits) >= 10 else digits


def _resolve_session_id_for_phone(vault_dir: Path, phone: str) -> str | None:
    """Resuelve el phone de una orden Medusa → session_id del vault
    (`wa_<phone>`), tolerante a discrepancias de country code y formato.

    Bug fix 2026-05-29: el phone que Medusa guarda en el shipping_address
    suele venir SIN country code (`3125671604`), mientras la sesión de
    WhatsApp usa el número internacional completo (`wa_573125671604`). El
    mapeo ingenuo `wa_<phone>` fallaba y el panel "Historial cliente" siempre
    mostraba "Sin datos" aunque el cliente tuviera episodios.

    Estrategia (de más barata a más robusta):
      1. **Fast path** — candidatos directos sin listar el dir:
         `wa_<digits>`, `wa_+<digits>`, `wa_57<digits>`, `wa_+57<digits>`.
         Cubre el caso común sin tocar el filesystem más de 4 stats.
      2. **Scan fallback** — si ninguno matcheó, recorre `vault_dir/wa_*` y
         compara `_phone_match_key` (últimos 10 dígitos). Maneja cualquier
         formato raro de country code que los candidatos directos no cubran.

    Returns el `session_id` (nombre del dir, e.g. `wa_573125671604`) o `None`
    si ningún match tiene `metadata.json`.
    """
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        # Demasiado corto para matchear con confianza — evitamos falsos
        # positivos (e.g. extensiones internas, números mal capturados).
        return None

    # 1. Fast path: candidatos directos (Colombia cc = 57).
    direct = [
        f"wa_{digits}",
        f"wa_+{digits}",
        f"wa_57{digits}",
        f"wa_+57{digits}",
    ]
    for cand in direct:
        if (vault_dir / cand / "metadata.json").exists():
            return cand

    # 2. Scan fallback: suffix-match sobre los últimos 10 dígitos.
    key = _phone_match_key(phone)
    if len(key) < 7 or not vault_dir.exists():
        return None
    try:
        for entry in vault_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("wa_"):
                continue
            if _phone_match_key(entry.name) != key:
                continue
            if (entry / "metadata.json").exists():
                return entry.name
    except OSError:
        # Vault no listable — degrada a None (panel mostrará "Sin datos").
        log.warning(
            "customer_score: no pude listar el vault %s para resolver phone",
            vault_dir,
        )
    return None


def _resolve_session_by_order_id(
    vault_dir: Path, backend_order_id: str
) -> str | None:
    """Reverse lookup: ¿qué sesión del vault tiene este order_id en sus
    episodios?

    El link canónico order↔cliente es que `register_order` anota el
    `order_id` en el episodio activo de la sesión (`episodes[].order_id`).
    Este reverse lookup recorre el vault y devuelve la sesión cuyo set de
    order_ids (de `episodes[]` + `registered_order`) contiene el target.

    Es la red de seguridad cuando `order.metadata.session_key` no está
    disponible (e.g. fue stripeado, o la order es vieja). Más confiable que
    el shipping phone porque NO depende del número de envío (que puede ser
    de un tercero o tener typos).

    O(N) reads de metadata.json — solo corre como fallback (la mayoría de
    las órdenes resuelven por session_key directo sin tocar esto).
    """
    if not vault_dir.exists():
        return None
    try:
        for entry in sorted(vault_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("wa_"):
                continue
            meta_file = entry / "metadata.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            if backend_order_id in _collect_order_ids_from_metadata(meta):
                return entry.name
    except OSError:
        log.warning(
            "customer_score: no pude escanear vault %s para reverse-lookup "
            "de order_id %s",
            vault_dir, backend_order_id,
        )
    return None


async def _resolve_session_for_order(
    *,
    backend_order_id: str,
    shipping_phone: str | None,
    vault_dir: Path,
) -> str | None:
    """Resuelve la sesión del cliente dueño de una orden, por orden de
    confiabilidad (de canónico a frágil):

      1. **`order.metadata.session_key`** — el link canónico. `register_order`
         lo escribe en la metadata de la draft/order al cerrar la venta
         (`metadata.session_key = wa_<phone>`). Es la identidad WhatsApp real
         del cliente, no el número de envío.

      2. **Reverse lookup por order_id** — si la metadata no tiene session_key
         (order vieja / stripeada), buscamos qué sesión referencia este
         order_id en sus `episodes[]`. Igual de confiable que (1), solo más
         caro (scan del vault).

      3. **Shipping phone** — último recurso, SOLO para órdenes creadas fuera
         del bot (manualmente en Medusa Admin) que no tienen session_key ni
         aparecen en ningún episodio. FRÁGIL: el phone de envío puede ser de
         un tercero (regalo) o tener typos — por eso es el último fallback,
         NO el primero.

    Bug fix 2026-05-29: antes resolvíamos SOLO por shipping phone (3), lo que
    causaba que dos órdenes del MISMO cliente con teléfonos de envío distintos
    mostraran historiales distintos (una con datos, otra "Sin datos"). El
    `metadata.session_key` elimina esa ambigüedad.
    """
    # 1. Canonical: order.metadata.session_key.
    # KeyError defensivo: un display_id sin resolver ("#6") hace que httpx
    # trate el "#" como fragment → el GET pega a la LISTA y la respuesta no
    # trae la key "order" (L-7b) — eso NO es MedusaAPIError y mataba el
    # resolver entero en silencio.
    try:
        client = get_medusa_client()
        raw = await client.get_order(backend_order_id, fields="id,metadata")
        session_key = (raw.get("metadata") or {}).get("session_key")
        # El dato viene de Medusa (editable en su Admin): mismo piso que un id
        # de URL — `wa_<real>/../../x` pasaba el `startswith("wa_")`.
        if (
            isinstance(session_key, str)
            and is_vault_session_id(session_key)
            and (vault_dir / session_key / "metadata.json").exists()
        ):
            return session_key
    except (MedusaAPIError, KeyError) as exc:
        log.info(
            "customer_score: get_order(%s) para session_key falló (%s) — "
            "probando fallbacks",
            backend_order_id, getattr(exc, "status_code", type(exc).__name__),
        )

    # 2. Reverse lookup por order_id en episodios del vault.
    by_order = _resolve_session_by_order_id(vault_dir, backend_order_id)
    if by_order is not None:
        return by_order

    # 3. Shipping phone (último recurso, frágil).
    if shipping_phone:
        return _resolve_session_id_for_phone(vault_dir, shipping_phone)

    return None


def _collect_order_ids_from_metadata(
    metadata: dict[str, Any],
) -> list[str]:
    """Extrae order_ids únicos de `episodes[].order_id` + legacy
    `registered_order.order_id`. Solo IDs Medusa válidos (filter `order_*`
    o `draft_*` prefix — descarta stubs HUB-* / AUDIT-* que NO están en
    Medusa)."""
    ids: set[str] = set()
    for ep in metadata.get("episodes") or []:
        oid = ep.get("order_id") if isinstance(ep, dict) else None
        if isinstance(oid, str) and (
            oid.startswith("order_") or oid.startswith("draft_")
        ):
            ids.add(oid)
    reg = metadata.get("registered_order")
    if isinstance(reg, dict) and reg.get("success") is True:
        oid = reg.get("order_id")
        if isinstance(oid, str) and (
            oid.startswith("order_") or oid.startswith("draft_")
        ):
            ids.add(oid)
    return sorted(ids)


async def _customer_order_facts(order_ids: list[str]):
    """Datos canónicos (OrderFacts) de los pedidos del cliente.

    Antes esto eran N GETs propios a Medusa (una segunda fuente para el
    mismo dato que ya sirve la vista Orders).
    Falla → snapshot `unresolved` y el scoring cae al camino por etiqueta.
    """
    from src.sdk.connectorkit import OrderFactsSnapshot, get_order_facts_port

    if not order_ids:
        return OrderFactsSnapshot()
    try:
        return await get_order_facts_port().get_facts(order_ids)
    except Exception:  # noqa: BLE001 — el score degrada, no 500-ea
        log.exception("customer_score: OrderFacts no disponible")
        return OrderFactsSnapshot(unresolved=frozenset(order_ids), stale=True)


def _ms_to_iso_date(ms: int) -> str:
    """ms epoch → YYYY-MM-DD UTC. El frontend formatea relativa ("hace 22 días")."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _empty_score_response() -> dict[str, Any]:
    """Score 'Sin datos' como dict — para early-returns del endpoint."""
    return {
        "tag": "Sin datos",
        "score_letter": "—",
        "score_value": 0,
        "score_reason": "No hay historial suficiente para calificar al cliente",
        "monetary_cop": 0,
        "last_purchase_at_ms": None,
        "last_purchase_iso": None,
        "frequency_total": 0,
        "episodes_total": 0,
        "rules_version": 0,
        "breakdown": [],
        "session_id": None,
    }


@router.post("/orders/{order_id}/customer-summary")
async def post_customer_summary(
    order_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """LLM on-demand summary del cliente. Costoso — solo se invoca cuando
    el operador clickea el botón "Resumir con IA" en el panel.

    NO cachea — siempre fresh para que cambios al metadata se reflejen.
    Si la llamada al LLM falla, devuelve fallback determinístico con
    `error_detail` poblado (frontend muestra warning).

    Response shape:
      {
        "summary": str,            # 2-3 oraciones
        "model": str,              # modelo que respondió
        "latency_ms": int,
        "rules_version": int,      # del score usado como input
        "error_detail": str | null
      }
    """
    # Mismo flow que /customer-score para obtener el session_id + score.
    qport = get_order_query_port()
    detail = await qport.get(order_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"Order {order_id!r} not found in Medusa.",
        )

    shipping_phone = None
    if detail.shipping_address is not None:
        shipping_phone = detail.shipping_address.phone
    if not shipping_phone:
        shipping_phone = detail.summary.phone

    session_id = await _resolve_session_for_order(
        backend_order_id=detail.summary.id,
        shipping_phone=shipping_phone,
        vault_dir=WORKSPACE_VAULT_DIR,
    )
    if session_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No pudimos linkear la orden a ninguna sesión de WhatsApp "
                "(sin metadata.session_key, sin episodio que la referencie, "
                "y el teléfono de envío no matchea) — el cliente no tiene "
                "historial."
            ),
        )
    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    metadata = metadata_store.read(session_id)

    # Compute score primero (cheap, deterministic) — el LLM lo usa como input.
    order_ids_in_episodes = _collect_order_ids_from_metadata(metadata)
    order_facts = await _customer_order_facts(order_ids_in_episodes)

    score_port = get_customer_scoring_port()
    score = score_port.score_session(
        session_id,
        now_ms=utc_now_ms(),
        order_facts=order_facts,
    )

    # Llamar al LLM (degrada graciosamente — el adapter NO levanta).
    summary_adapter = get_customer_summary_adapter()
    result = await summary_adapter.summarize(score=score, metadata=metadata)
    return asdict(result)


@router.get("/orders-health")
async def orders_health() -> dict[str, Any]:
    """Sanity-check del port — util para verificar config en prod sin
    consumir cuota de Medusa. Devuelve qué tipo de port esta inyectado
    y si Medusa responde en una llamada vacia."""
    port = get_order_query_port()
    port_name = type(port).__name__
    # Intentar una list vacia — captura errores de auth / network sin
    # consumir mucho (limit=1).
    probe = await port.list(limit=1, offset=0, include_drafts=False)
    return {
        "port": port_name,
        "catalog_available": probe.catalog_available,
        "error_detail": probe.error_detail,
        "sample_count": probe.count,
    }
