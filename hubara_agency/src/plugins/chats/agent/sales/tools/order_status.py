"""Tool: CheckOrderStatusTool — estado de los pedidos del cliente.

Convivencia ETA/Sales (2026-06-10): el agente ETA es un notificador puro y NO
posee la conversación — cuando el cliente pregunta "¿cuándo llega mi pedido?",
quien responde es SALES con esta tool.

Fuentes de datos (híbrido, 2026-07-17 — HU scheduler post-venta):

1. **Vault (local, sin red)**: ``metadata.eta_tracking`` (estado que mantiene
   el notificador ETA por pedido) + los order ids de ``episodes[].order_id``
   y ``registered_order`` — así una orden recién registrada/pagada aparece
   AUNQUE todavía no esté en seguimiento de entrega (antes la tool decía
   "no tiene pedidos" en el caso post-compra devuelta al bot).
2. **Port de orders (en vivo, opcional)**: si el composition root inyecta un
   ``OrderQueryPort`` (duck-typed — sin import del plugin orders, R-DIP), se
   enriquece cada pedido con la etapa y el ``pay_status`` REALES. L-2: Medusa
   en Railway puede tardar 2-30s — cada lookup corre con timeout corto y ante
   fallo la tool degrada a lo local SIN reventar. El guion gobierna el
   wording: nunca afirmar "pago confirmado" si ``pay_status != "paid"``.

Acoplamiento: a la CLAVE ``eta_tracking`` del metadata compartido de la
sesión (string-based, mismo nivel soft que los eventos del dispatcher) — NO
importa código del plugin eta (P-3).

DEHA / ADR-001: tool inerte — lee vault + port inyectado y devuelve un
envelope JSON; no toca Temporal ni muta estado.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.config import WORKSPACE_VAULT_DIR

# Wording de cara al cliente (es de SALES — puede divergir del label interno
# del board ETA sin romper nada).
_STAGE_LABELS: dict[str, str] = {
    "new": "recibido",
    "preparing": "en preparación",
    "ready": "listo para envío",
    "shipping": "en camino",
    "delivered": "entregado",
    "cancelled": "cancelado",
}

#: L-2: timeout por lookup en vivo — Medusa@Railway puede colgarse; la tool
#: responde con lo local antes de reventar el turno del LLM.
_LIVE_TIMEOUT_S = 8.0

#: tope de pedidos por respuesta (paridad con el cap del endpoint by-session).
_MAX_ORDERS = 5


def _fmt_when(at_ms: Any) -> str | None:
    try:
        dt = datetime.fromtimestamp(int(at_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _tracking_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    tracking = data.get("eta_tracking") if isinstance(data, dict) else None
    if not isinstance(tracking, dict):
        return []
    orders = tracking.get("orders")
    if isinstance(orders, dict):  # shape v2 multi-pedido
        return [v for v in orders.values() if isinstance(v, dict)]
    if tracking.get("order_id"):  # shape v1 legacy
        return [tracking]
    return []


def _metadata_order_ids(data: dict[str, Any]) -> list[str]:
    """Order ids registrados en la sesión (episodios + registro legacy)."""
    ids: list[str] = []
    episodes = data.get("episodes")
    if isinstance(episodes, list):
        for ep in episodes:
            oid = ep.get("order_id") if isinstance(ep, dict) else None
            if isinstance(oid, str) and oid and oid not in ids:
                ids.append(oid)
    registered = data.get("registered_order")
    if isinstance(registered, dict):
        oid = registered.get("order_id")
        if isinstance(oid, str) and oid and oid not in ids:
            ids.append(oid)
    return ids


class CheckOrderStatusTool(ToolBase):
    """Consulta el estado de los pedidos del cliente actual."""

    name = "check_order_status"
    description = (
        "Devuelve el estado actual de los pedidos del cliente de ESTA "
        "conversación: etapa de entrega y estado del pago (pay_status). "
        "Úsala cuando el cliente pregunte por su pedido: cuándo llega, en "
        "qué estado está, si ya salió, si el pago quedó registrado. Si el "
        "cliente tiene varios pedidos, la respuesta los lista todos — "
        "menciona el número de pedido al responder. NUNCA afirmes que el "
        "pago está confirmado salvo que pay_status sea 'paid'. Si no hay "
        "pedidos, dilo y ofrece ayuda para comprar."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(
        self,
        workspace: str | Path,
        vault_dir: str | Path | None = None,
        query_port: Any | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )
        # OrderQueryPort duck-typed (`async get(order_id) -> detail | None`) —
        # inyectado por el composition root del worker; None = solo local.
        self._query_port = query_port

    async def _live_summary(self, order_id: str) -> Any | None:
        """Lookup en vivo tolerante: None ante timeout/error (L-2)."""
        if self._query_port is None:
            return None
        try:
            detail = await asyncio.wait_for(
                self._query_port.get(order_id), timeout=_LIVE_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001 — degradar a local, jamás reventar
            return None
        return getattr(detail, "summary", None) if detail is not None else None

    async def execute_with_context(self, ctx: ToolContext) -> str:
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        if not isinstance(data, dict):
            data = {}

        # 1. Base local: tracking de entrega + ids registrados sin tracking.
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for entry in _tracking_entries(data):
            oid = str(entry.get("order_id") or "")
            if not oid or oid in by_id:
                continue
            stage = str(entry.get("current_stage") or "new")
            events = entry.get("events") or []
            last_event = events[-1] if isinstance(events, list) and events else {}
            by_id[oid] = {
                "order_id": oid,
                "status": _STAGE_LABELS.get(stage, stage),
                "status_code": stage,
                "last_update": _fmt_when(
                    (last_event or {}).get("at_ms") or entry.get("started_at_ms")
                ),
                "stages_notified": list(entry.get("notified_stages") or []),
            }
            order.append(oid)
        for oid in _metadata_order_ids(data):
            if oid not in by_id:
                by_id[oid] = {
                    "order_id": oid,
                    "status": "registrado",
                    "status_code": None,
                    "last_update": None,
                    "stages_notified": [],
                }
                order.append(oid)

        if not by_id:
            return json.dumps(
                {
                    "orders": [],
                    "note": (
                        "El cliente no tiene pedidos en seguimiento de entrega "
                        "en esta conversación."
                    ),
                },
                ensure_ascii=False,
            )

        # 2. Enriquecimiento en vivo (etapa + pago reales) — best-effort.
        live_unavailable = False
        for oid in order[:_MAX_ORDERS]:
            summary = await self._live_summary(oid)
            if summary is None:
                live_unavailable = self._query_port is not None
                continue
            out = by_id[oid]
            stage = getattr(summary, "status", None)
            if isinstance(stage, str) and stage:
                out["status"] = _STAGE_LABELS.get(stage, stage)
                out["status_code"] = stage
            pay_status = getattr(summary, "pay_status", None)
            if isinstance(pay_status, str) and pay_status:
                out["pay_status"] = pay_status
                out["payment_confirmed"] = pay_status == "paid"
            display_id = getattr(summary, "display_id", None)
            if isinstance(display_id, str) and display_id:
                out["display_id"] = display_id
            total_cop = getattr(summary, "total_cop", None)
            if isinstance(total_cop, int):
                out["total_cop"] = total_cop

        payload: dict[str, Any] = {
            "orders": [by_id[oid] for oid in order[:_MAX_ORDERS]]
        }
        if live_unavailable:
            payload["note"] = (
                "No se pudo consultar el estado en vivo de todos los pedidos; "
                "los datos mostrados son los últimos conocidos."
            )
        return json.dumps(payload, ensure_ascii=False)
