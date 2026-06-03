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
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Path, Query

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
    ScheduleDeliveryCommand,
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

router = APIRouter()
log = logging.getLogger(__name__)


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
    cmd = TransitionStageCommand(
        order_id=order_id,
        to_stage=stage_raw,  # type: ignore[arg-type]
        note=body.get("note")
        if isinstance(body.get("note"), str)
        else None,
        force=bool(body.get("force", False)),
    )
    port = get_order_command_port()
    result = await port.transition_stage(cmd)
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
    totals_cop: dict[str, int] = {}
    created_ats_ms: dict[str, int] = {}
    if order_ids_in_episodes:
        client = get_medusa_client()
        results = await asyncio.gather(
            *(
                _fetch_order_total_and_date(client, oid)
                for oid in order_ids_in_episodes
            ),
            return_exceptions=True,
        )
        for oid, res in zip(order_ids_in_episodes, results):
            if isinstance(res, tuple):
                total_cop, created_at_ms = res
                if total_cop is not None:
                    totals_cop[oid] = total_cop
                if created_at_ms is not None:
                    created_ats_ms[oid] = created_at_ms

    # Score.
    score_port = get_customer_scoring_port()
    score = score_port.score_session(
        session_id,
        now_ms=utc_now_ms(),
        medusa_order_totals_cop=totals_cop,
        medusa_order_created_at_ms=created_ats_ms,
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
    try:
        client = get_medusa_client()
        raw = await client.get_order(backend_order_id, fields="id,metadata")
        session_key = (raw.get("metadata") or {}).get("session_key")
        if (
            isinstance(session_key, str)
            and session_key.startswith("wa_")
            and (vault_dir / session_key / "metadata.json").exists()
        ):
            return session_key
    except MedusaAPIError as exc:
        log.info(
            "customer_score: get_order(%s) para session_key falló (%s) — "
            "probando fallbacks",
            backend_order_id, getattr(exc, "status_code", "?"),
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


async def _fetch_order_total_and_date(
    client, order_id: str
) -> tuple[int | None, int | None]:
    """Fetch `total` (COP major) + `created_at` (ms epoch) para un order_id.

    Devuelve `(None, None)` si Medusa 404 / 5xx (el order_id existe en el
    vault pero ya no en Medusa — e.g. fue borrado manualmente).

    Bug fix 2026-05-26 paralelo: usamos `client.get_order(order_id, fields=...)`
    que retry-ea con tenacity por debajo. NO levanta excepción acá —
    `asyncio.gather(..., return_exceptions=True)` captura los errores y
    el caller los filtra.
    """
    try:
        data = await client.get_order(
            order_id, fields="id,total,created_at,currency_code"
        )
    except MedusaAPIError as exc:
        log.info(
            "customer_score: order %s no fetcheable (%s) — skip",
            order_id, exc.status_code,
        )
        return None, None

    total_raw = data.get("total")
    total_cop: int | None = None
    if isinstance(total_raw, (int, float)) and total_raw > 0:
        # Medusa stores amounts en minor units para algunos providers, en
        # major para CO/MX. El query adapter ya tiene `_to_int_cop` que
        # heurística decide; acá replicamos la misma lógica simple:
        # int() truncado preserva el major-unit behavior por default.
        total_cop = int(total_raw)

    created_at_ms: int | None = None
    created_at_str = data.get("created_at")
    if isinstance(created_at_str, str):
        try:
            dt = datetime.fromisoformat(
                created_at_str.replace("Z", "+00:00")
            )
            created_at_ms = int(dt.timestamp() * 1000)
        except (ValueError, OSError):
            created_at_ms = None
    return total_cop, created_at_ms


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
    totals_cop: dict[str, int] = {}
    created_ats_ms: dict[str, int] = {}
    if order_ids_in_episodes:
        client = get_medusa_client()
        results = await asyncio.gather(
            *(
                _fetch_order_total_and_date(client, oid)
                for oid in order_ids_in_episodes
            ),
            return_exceptions=True,
        )
        for oid, res in zip(order_ids_in_episodes, results):
            if isinstance(res, tuple):
                total_cop, created_at_ms = res
                if total_cop is not None:
                    totals_cop[oid] = total_cop
                if created_at_ms is not None:
                    created_ats_ms[oid] = created_at_ms

    score_port = get_customer_scoring_port()
    score = score_port.score_session(
        session_id,
        now_ms=utc_now_ms(),
        medusa_order_totals_cop=totals_cop,
        medusa_order_created_at_ms=created_ats_ms,
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
