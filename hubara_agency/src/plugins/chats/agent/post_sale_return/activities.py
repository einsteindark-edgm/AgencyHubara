"""Activities del scheduler post-venta — el ÚNICO lugar con I/O del ciclo.

Solo imports `src.sdk` (P-28). El plan puro vive en `use_cases.py`. Dos seams:

* `scan_post_sale_human_sessions_activity` — vault scan (dirs `wa_*`,
  metadata tolerante a corruptos) + filtro puro.
* `return_post_sale_session_to_sales_activity` — la mutación del botón
  "devolver al robot" (`chats/api/handoff.py::return_to_bot`, rama ventas)
  en batch: primero verifica contra Temporal que NO haya robot corriendo
  para la sesión, después muta metadata bajo el lock de `update()`
  re-chequeando el predicado fresco (un webhook/operador pudo escribir
  entre el scan y este write — a diferencia del botón, acá no hay humano
  mirando la pantalla).
"""
from __future__ import annotations

import json
import time
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from src.plugins.chats.agent.post_sale_return.use_cases import (
    is_returnable,
    select_post_sale_sessions,
)
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    get_temporal_client,
)

#: prefijo de sesiones WhatsApp en el vault (los demás dirs se saltan).
_SESSION_PREFIX = "wa_"

#: mismos valores que escribe el endpoint return-to-bot (rama ventas).
_RETURN_TAG = "RETOMA_VENTA"
_ROUTE_VENTAS = "ventas"
_MOTIVO = "Scheduler post-venta devolvió la conversación a Sales (compra ya cerrada)"

#: resultados de la activity per-session (viajan al summary del workflow).
RESULT_RETURNED = "returned"
RESULT_SKIPPED_ROBOT_RUNNING = "skipped_robot_running"
RESULT_SKIPPED_STATE_CHANGED = "skipped_state_changed"


@activity.defn(name="scan_post_sale_human_sessions")
async def scan_post_sale_human_sessions_activity() -> list[str]:
    """Sesiones con `tag=COMPRA_EXITOSA` + `active_route=humano` en el vault."""
    sessions: list[tuple[str, dict[str, Any]]] = []
    vault = WORKSPACE_VAULT_DIR
    if vault.exists():
        for session_dir in sorted(vault.iterdir()):
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith(_SESSION_PREFIX):
                continue
            try:
                metadata = json.loads(
                    (session_dir / "metadata.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(metadata, dict):
                sessions.append((session_dir.name, metadata))
    selected = select_post_sale_sessions(sessions)
    activity.logger.info(
        "post-sale-return scan: %s sesiones en el vault, %s candidatas "
        "(COMPRA_EXITOSA en humano).",
        len(sessions),
        len(selected),
    )
    return selected


def _is_not_found(exc: RPCError) -> bool:
    """Réplica local de `_is_not_found` del dispatcher de orchestration
    (P-28 prohíbe importarlo de platform): NOT_FOUND por status code +,
    defensivamente, la firma textual del backend postgres."""
    if getattr(exc, "status", None) == RPCStatusCode.NOT_FOUND:
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "no rows in result set" in msg or "workflow not found" in msg


async def _robot_running(client: Client, workflow_id: str) -> bool:
    """RUNNING real en Temporal. Solo NOT_FOUND cuenta como "no corre";
    un RPC transitorio (UNAVAILABLE/DEADLINE) PROPAGA — absorberlo mutaría
    una sesión que puede tener el bot vivo (el retry del workflow reintenta)."""
    try:
        desc = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as exc:
        if _is_not_found(exc):
            return False
        raise
    return desc.status == WorkflowExecutionStatus.RUNNING


@activity.defn(name="return_post_sale_session_to_sales")
async def return_post_sale_session_to_sales_activity(session_id: str) -> str:
    """Devuelve UNA sesión al bot de ventas (idempotente vía re-check).

    Reintentos de Temporal son seguros: tras un return exitoso el predicado
    ya no matchea y el segundo intento termina en `skipped_state_changed`.
    """
    client = await get_temporal_client()
    for workflow_id in (f"session-{session_id}", f"remarketing-{session_id}"):
        if await _robot_running(client, workflow_id):
            activity.logger.info(
                "post-sale-return: %s tiene %s RUNNING — no se toca.",
                session_id,
                workflow_id,
            )
            return RESULT_SKIPPED_ROBOT_RUNNING

    def _return_to_sales(data: dict[str, Any]) -> dict[str, Any] | None:
        # Re-check FRESCO bajo el lock con el predicado COMPLETO (incluye pago
        # confirmado): si el estado cambió desde el scan (humano retomó,
        # cliente escribió y el ingest movió la ruta) o no hay pago verificado,
        # abortar sin escribir — nunca pisar una conversación viva ni devolver
        # una venta sin pago confirmado.
        if not is_returnable(data):
            return None
        # Misma mutación que `_append_status` del endpoint return-to-bot.
        data["tag"] = _RETURN_TAG
        data["motivo"] = _MOTIVO
        data["active_route"] = _ROUTE_VENTAS
        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": _RETURN_TAG,
                "motivo": _MOTIVO,
                "active_route": _ROUTE_VENTAS,
                "timestamp": time.time(),
                "source": "post_sale_return_scheduler",
            }
        )
        return data

    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    written = store.update(session_id, _return_to_sales)
    if written is None:
        activity.logger.info(
            "post-sale-return: %s cambió de estado entre scan y lock — skip.",
            session_id,
        )
        return RESULT_SKIPPED_STATE_CHANGED
    activity.logger.info(
        "post-sale-return: %s devuelta a Sales (tag=%s).", session_id, _RETURN_TAG
    )
    return RESULT_RETURNED
