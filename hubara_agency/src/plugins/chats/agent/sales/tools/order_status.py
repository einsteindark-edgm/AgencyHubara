"""Tool: CheckOrderStatusTool — estado de los pedidos del cliente.

Convivencia ETA/Sales (2026-06-10): el agente ETA es un notificador puro y NO
posee la conversación — cuando el cliente pregunta "¿cuándo llega mi pedido?",
quien responde es SALES con esta tool.

Fuente de datos: ``metadata.eta_tracking`` de la sesión (vault) — el estado
compartido que el notificador ETA mantiene por pedido (multi-pedido, shape
``{"orders": {order_id: {current_stage, notified_stages, events, ...}}}``).
Es lectura LOCAL (sin red): refleja lo último notificado al cliente, que es
la verdad operativa. Deliberadamente NO consulta Medusa en el turno (L-2: el
list endpoint tarda 2-30s en Railway y reventaría el timeout de la tool).

Acoplamiento: a la CLAVE ``eta_tracking`` del metadata compartido de la
sesión (string-based, mismo nivel soft que los eventos del dispatcher) — NO
importa código del plugin eta (P-3). Si eta está apagado o no hay tracking,
degrada limpio: "sin pedidos en seguimiento".

DEHA / ADR-001: tool inerte — lee el vault y devuelve un envelope JSON; no
toca Temporal ni muta estado.
"""
from __future__ import annotations

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


def _fmt_when(at_ms: Any) -> str | None:
    try:
        dt = datetime.fromtimestamp(int(at_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M UTC")


class CheckOrderStatusTool(ToolBase):
    """Consulta el estado de los pedidos en seguimiento del cliente actual."""

    name = "check_order_status"
    description = (
        "Devuelve el estado actual de los pedidos del cliente de ESTA "
        "conversación (los que están en seguimiento de entrega). Úsala "
        "cuando el cliente pregunte por su pedido: cuándo llega, en qué "
        "estado está, si ya salió. Si el cliente tiene varios pedidos, la "
        "respuesta los lista todos — menciona el número de pedido al "
        "responder. Si no hay pedidos en seguimiento, dilo y ofrece "
        "ayuda para comprar."
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
    ) -> None:
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )

    async def execute_with_context(self, ctx: ToolContext) -> str:
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        tracking = data.get("eta_tracking") if isinstance(data, dict) else None
        entries: list[dict[str, Any]] = []
        if isinstance(tracking, dict):
            orders = tracking.get("orders")
            if isinstance(orders, dict):  # shape v2 multi-pedido
                entries = [v for v in orders.values() if isinstance(v, dict)]
            elif tracking.get("order_id"):  # shape v1 legacy
                entries = [tracking]

        if not entries:
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

        orders_out: list[dict[str, Any]] = []
        for entry in entries:
            stage = str(entry.get("current_stage") or "new")
            events = entry.get("events") or []
            last_event = events[-1] if isinstance(events, list) and events else {}
            orders_out.append(
                {
                    "order_id": entry.get("order_id"),
                    "status": _STAGE_LABELS.get(stage, stage),
                    "status_code": stage,
                    "last_update": _fmt_when(
                        (last_event or {}).get("at_ms") or entry.get("started_at_ms")
                    ),
                    "stages_notified": list(entry.get("notified_stages") or []),
                }
            )

        return json.dumps({"orders": orders_out}, ensure_ascii=False)
