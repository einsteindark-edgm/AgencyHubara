"""HTTP API del plugin `catalog` — disparar + observar el sync de catálogo.

El dashboard (sección Catalog) usa estos endpoints para reemplazar el viejo
wizard mock por el sync REAL: `CatalogSyncWorkflow` (Temporal) que trae los
productos de Medusa, escribe la copia local (`snapshot.json` + `by_handle/` +
`manifest.json` que lee el agente Sales) y propaga a Meta Commerce Catalog.

Expone:
  POST /api/catalog/sync           → dispara un sync on-demand (idempotente:
                                      si ya hay uno corriendo lo reusa).
  GET  /api/catalog/sync/{wf_id}   → estado + step-by-step en vivo de ese run.
  GET  /api/catalog/syncs          → historial de syncs (Temporal visibility).
  GET  /api/catalog/snapshot       → estado de la copia local actual (la que
                                      consume Sales): versión, # productos,
                                      antigüedad, stale.

Por qué el handler puede tocar Temporal: un endpoint HTTP NO es un workflow ni
una tool, así que `get_temporal_client()` es legítimo acá (R-DIP solo prohíbe
el cliente Temporal dentro de workflows/tools). Mismo patrón que
`orders.api._emit_stage_changed_event`.

El workflow se arranca por NOMBRE (`"CatalogSyncWorkflow"`) en vez de por
referencia para mantener el grafo de import del proceso HTTP mínimo (no
arrastra el módulo de activities). El único import del plugin es el DTO de
input (`CatalogSyncInput`), un `@dataclass(frozen=True)` sin side-effects.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir
from src.platform.plugin_manifest import get_task_queue
from src.platform.temporal.client import get_temporal_client
from src.plugins.catalog.agent.contracts import CatalogSyncInput

router = APIRouter()
log = logging.getLogger(__name__)

# Nombre registrado del workflow (ver `@workflow.defn(name=...)` en
# agent/workflows/sync.py). Arrancar por string evita importar la clase (y su
# grafo de activities) en el proceso HTTP.
_WORKFLOW_NAME = "CatalogSyncWorkflow"

# Pasos reales del workflow — fallback para pintar la UI cuando la query
# `progress` todavía no está disponible (el worker aún no tomó el run). La
# fuente de verdad en vivo es la query del propio workflow.
_STEP_LABELS: tuple[tuple[str, str], ...] = (
    ("pull", "Traer productos de Medusa"),
    ("write", "Escribir copia local (snapshot)"),
    ("push", "Propagar a Meta Commerce Catalog"),
)

# Mapeo del enum de Temporal → string estable que consume el frontend.
_STATUS_MAP: dict[WorkflowExecutionStatus, str] = {
    WorkflowExecutionStatus.RUNNING: "running",
    WorkflowExecutionStatus.COMPLETED: "completed",
    WorkflowExecutionStatus.FAILED: "failed",
    WorkflowExecutionStatus.CANCELED: "cancelled",
    WorkflowExecutionStatus.TERMINATED: "cancelled",
    WorkflowExecutionStatus.TIMED_OUT: "failed",
    WorkflowExecutionStatus.CONTINUED_AS_NEW: "running",
}


# ─── Helpers ───────────────────────────────────────────────────────────────


def _map_status(status: WorkflowExecutionStatus | None) -> str:
    return _STATUS_MAP.get(status, "unknown") if status is not None else "unknown"


def _dt_ms(dt: datetime | None) -> int | None:
    """datetime (tz-aware UTC de Temporal) → ms epoch, o None."""
    return int(dt.timestamp() * 1000) if dt is not None else None


def _default_steps() -> list[dict[str, str]]:
    return [
        {"key": key, "label": label, "status": "pending", "detail": ""}
        for key, label in _STEP_LABELS
    ]


def _is_not_found(exc: RPCError) -> bool:
    """True si un ``RPCError`` indica que el workflow_id no existe.

    Mismo criterio que `platform/orchestration/dispatcher._is_not_found`:
    status code (robusto) + firma textual (`"no rows in result set"` con el
    backend SQLite/postgres local) por si cambia entre versiones del server.
    """
    if getattr(exc, "status", None) == RPCStatusCode.NOT_FOUND:
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "no rows in result set" in msg or "workflow not found" in msg


def _triggered_by(workflow_id: str) -> str:
    """Extrae la etiqueta de auditoría del workflow_id.

    Formato: ``catalog-sync-on-demand-{triggered_by}-{timestamp}``. El
    timestamp es el segmento final numérico.
    """
    prefix = "catalog-sync-on-demand-"
    if workflow_id.startswith(prefix):
        rest = workflow_id[len(prefix):]
        head, _, tail = rest.rpartition("-")
        if head and tail.isdigit():
            return head
        return rest
    return workflow_id


async def _find_running_sync(client: Client) -> str | None:
    """workflow_id de un `CatalogSyncWorkflow` RUNNING, o None.

    Best-effort: si la visibility no soporta el filtro (Temporal local viejo),
    devolvemos None y el caller arranca uno nuevo igual.
    """
    try:
        query = (
            f'WorkflowType = "{_WORKFLOW_NAME}" AND ExecutionStatus = "Running"'
        )
        async for wf in client.list_workflows(query):
            return wf.id
    except Exception as exc:  # noqa: BLE001 — visibility opcional
        log.info("catalog.sync: list_workflows(running) no disponible: %s", exc)
    return None


async def _failure_message(handle: Any) -> str | None:
    """Mensaje de la falla de un workflow cerrado como FAILED.

    El workflow ya está cerrado → `result()` resuelve rápido lanzando la
    `WorkflowFailureError`. Devolvemos el texto de la causa (recortado).
    """
    try:
        await handle.result()
        return None
    except Exception as exc:  # noqa: BLE001 — extraemos el texto para la UI
        cause = getattr(exc, "cause", None)
        return str(cause or exc)[:300]


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/sync")
async def trigger_sync() -> dict[str, Any]:
    """Dispara un catalog sync on-demand (Medusa → copia local → Meta).

    Idempotente por diseño: si ya hay un `CatalogSyncWorkflow` corriendo,
    NO arranca otro (el worker es single-writer — dos writes concurrentes
    correrían carrera en `os.replace`). Devuelve el run existente con
    `already_running=true` para que el dashboard simplemente lo poll-ee.

    Response:
      {
        "workflow_id": "catalog-sync-on-demand-dashboard-1717...",
        "run_id": "01HX...",
        "started_at_ms": 1717...,
        "status": "running",
        "already_running": false
      }
    """
    client = await get_temporal_client()

    existing = await _find_running_sync(client)
    if existing is not None:
        return {
            "workflow_id": existing,
            "run_id": "",
            "started_at_ms": int(time.time() * 1000),
            "status": "running",
            "already_running": True,
        }

    snapshot_dir = str(get_snapshot_dir())
    workflow_id = f"catalog-sync-on-demand-dashboard-{int(time.time())}"
    try:
        handle = await client.start_workflow(
            _WORKFLOW_NAME,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=snapshot_dir,
            ),
            id=workflow_id,
            task_queue=get_task_queue("catalog", "sync"),
        )
    except Exception as exc:  # noqa: BLE001 — superficie el error al operador
        log.warning("catalog.sync: start_workflow falló: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"No pude arrancar el sync en Temporal: {exc}",
        ) from exc

    return {
        "workflow_id": workflow_id,
        "run_id": getattr(handle, "first_execution_run_id", "") or "",
        "started_at_ms": int(time.time() * 1000),
        "status": "running",
        "already_running": False,
    }


@router.get("/sync/{workflow_id}")
async def get_sync_status(
    workflow_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    """Estado + step-by-step en vivo de un sync.

    Combina `describe()` (status canónico + tiempos) con la query `progress`
    del workflow (corre tanto en RUNNING como en COMPLETED vía replay). Si el
    workflow falló, marca el paso que estaba `running` como `failed` y expone
    el mensaje de la falla.

    Response:
      {
        "workflow_id", "status", "started_at_ms", "finished_at_ms",
        "product_count", "version", "error",
        "steps": [{ "key", "label", "status", "detail" }, ...]
      }
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    try:
        desc = await handle.describe()
    except RPCError as exc:
        if _is_not_found(exc):
            raise HTTPException(
                status_code=404, detail=f"sync {workflow_id!r} no encontrado"
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"temporal_error: {exc}"
        ) from exc

    status = _map_status(desc.status)
    steps = _default_steps()
    product_count = 0
    version = ""
    error: str | None = None

    # Query `progress` — fuente de verdad del step-by-step. Puede no estar lista
    # si el worker todavía no tomó el run (queda el fallback pending).
    try:
        raw = await handle.query("progress")
        payload = json.loads(raw)
        if isinstance(payload.get("steps"), list) and payload["steps"]:
            steps = payload["steps"]
        product_count = int(payload.get("product_count") or 0)
        version = str(payload.get("version") or "")
        error = payload.get("error")
    except Exception as exc:  # noqa: BLE001 — query best-effort
        log.info("catalog.sync: query(progress) de %s falló: %s", workflow_id, exc)

    # Si falló, el paso en curso quedó `running` en el snapshot de progreso —
    # lo marcamos `failed` y traemos el mensaje real de Temporal.
    if status == "failed":
        msg = await _failure_message(handle)
        error = error or msg
        for step in steps:
            if step.get("status") == "running":
                step["status"] = "failed"
                if msg and not step.get("detail"):
                    step["detail"] = msg

    return {
        "workflow_id": workflow_id,
        "status": status,
        "started_at_ms": _dt_ms(desc.start_time),
        "finished_at_ms": _dt_ms(desc.close_time),
        "product_count": product_count,
        "version": version,
        "error": error,
        "steps": steps,
    }


@router.get("/syncs")
async def list_syncs(limit: int = 30) -> dict[str, Any]:
    """Historial de syncs para el panel izquierdo del dashboard.

    Fuente: Temporal visibility (`list_workflows`) filtrado por tipo. Si la
    visibility no soporta el filtro (Temporal local viejo), devolvemos
    `available=false` + lista vacía para que la UI muestre un estado vacío
    explícito en lugar de un error (mismo patrón que `catalog_available` en
    orders).

    Response:
      {
        "syncs": [{ "workflow_id", "run_id", "status",
                    "started_at_ms", "finished_at_ms", "triggered_by" }, ...],
        "available": true,
        "error_detail": null
      }
    """
    limit = max(1, min(limit, 100))
    client = await get_temporal_client()
    out: list[dict[str, Any]] = []
    try:
        query = f'WorkflowType = "{_WORKFLOW_NAME}"'
        async for wf in client.list_workflows(query):
            out.append(
                {
                    "workflow_id": wf.id,
                    "run_id": wf.run_id,
                    "status": _map_status(wf.status),
                    "started_at_ms": _dt_ms(wf.start_time),
                    "finished_at_ms": _dt_ms(wf.close_time),
                    "triggered_by": _triggered_by(wf.id),
                }
            )
            if len(out) >= limit:
                break
    except Exception as exc:  # noqa: BLE001 — visibility opcional
        log.info("catalog.sync: list_workflows no disponible: %s", exc)
        return {"syncs": [], "available": False, "error_detail": str(exc)}

    # Orden: más reciente primero (la visibility no garantiza orden).
    out.sort(key=lambda s: s["started_at_ms"] or 0, reverse=True)
    return {"syncs": out, "available": True, "error_detail": None}


@router.get("/snapshot")
async def get_snapshot() -> dict[str, Any]:
    """Estado de la copia local actual — la que consume el agente Sales.

    Lee `manifest.json` del snapshot dir. Se muestra aunque no haya ningún
    sync corriendo (es el estado "en reposo" del catálogo local).

    Response:
      {
        "exists": bool, "version": str|null, "product_count": int,
        "fetched_at": str|null, "age_minutes": int|null, "stale": bool,
        "max_age_minutes": int, "snapshot_dir": str
      }
    """
    snap_dir = get_snapshot_dir()
    max_age = get_max_age_minutes()
    manifest_path = snap_dir / "manifest.json"

    if not manifest_path.exists():
        return {
            "exists": False,
            "version": None,
            "product_count": 0,
            "fetched_at": None,
            "age_minutes": None,
            "stale": True,
            "max_age_minutes": max_age,
            "snapshot_dir": str(snap_dir),
        }

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("catalog.snapshot: manifest ilegible: %s", exc)
        return {
            "exists": False,
            "version": None,
            "product_count": 0,
            "fetched_at": None,
            "age_minutes": None,
            "stale": True,
            "max_age_minutes": max_age,
            "snapshot_dir": str(snap_dir),
        }

    fetched_at = manifest.get("fetched_at")
    age_minutes: int | None = None
    stale = False
    if isinstance(fetched_at, str):
        try:
            dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt
            age_minutes = int(delta.total_seconds() // 60)
            stale = age_minutes >= max_age
        except ValueError:
            age_minutes = None

    return {
        "exists": True,
        "version": manifest.get("version"),
        "product_count": int(manifest.get("product_count") or 0),
        "fetched_at": fetched_at,
        "age_minutes": age_minutes,
        "stale": stale,
        "max_age_minutes": max_age,
        "snapshot_dir": str(snap_dir),
    }
