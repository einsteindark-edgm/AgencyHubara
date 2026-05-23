"""Tool: ManageConversationTagTool.

DEHA-compliant tool that satisfies the exoclaw `Tool` Protocol via the
`ToolBase` mixin. Implements `execute_with_context(ctx, **params)` so that the
`ToolRegistry.execute` dispatch (`exoclaw.agent.tools.registry:102-105`) injects
the `ToolContext` automatically.

The tag taxonomy (`INTERESADO`, `RECHAZO`, `COMPRA_EXITOSA`) is enforced by the
JSON schema `enum` constraint, not by post-hoc `if not isinstance(...)` defaults.
That means an invalid tag returns `Error: Invalid parameters ...` to the LLM
(via `ToolBase.validate_params`) — the legacy silent fallback to `INTERESADO`
is gone. Same for `motivo`: required, non-empty.

ADR-001: the tool is inert w.r.t. Temporal. It writes the tag to `metadata.json`
and returns a JSON envelope (`schedule_remarketing` + `message`); the workflow
parses the envelope and issues the dispatcher activity. No `temporal_client`,
no `start_workflow`, no workflow imports.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_VENTAS


# Sesión c4e3416f: `CONFIRMADO_SIN_DATOS` es para el caso donde el cliente
# confirmó el pedido (apretó "Confirmar" en `present_order_confirmation`) pero
# NO completó los datos de envío y dejó la conversación. Esta tag NO arranca
# remarketing (a diferencia de INTERESADO) — el LLM la usa SIEMPRE en combo
# con `escalate_to_human(reason_category="ORDER_PENDING_SHIPPING_DETAILS")`
# para que un humano cierre la operación pidiendo los datos faltantes.
_TAG_ENUM: list[str] = [
    "INTERESADO",
    "RECHAZO",
    "COMPRA_EXITOSA",
    "CONFIRMADO_SIN_DATOS",
]


class ManageConversationTagTool(ToolBase):
    """Register the final commercial tag for a conversation, plus the reason.

    Used at end-of-sale or when the user loses interest. If the tag is
    `INTERESADO`, the response envelope carries a `schedule_remarketing` decision
    that the workflow turns into a dispatcher-activity invocation.
    """

    name = "manage_conversation_tag"
    description = (
        "Úsala al final de la venta, o si el usuario pierde el interés. "
        "Registra la etiqueta final ('INTERESADO', 'RECHAZO', "
        "'COMPRA_EXITOSA', 'CONFIRMADO_SIN_DATOS') y un resumen breve."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "enum": _TAG_ENUM,
                "description": (
                    "Etiqueta final. Una de: INTERESADO (cliente sigue "
                    "dudando o se enfrió — programa remarketing 1 hora "
                    "después), RECHAZO (no compra, cierre definitivo), "
                    "COMPRA_EXITOSA (cierre con venta concretada — datos "
                    "de envío recibidos + pago resuelto), "
                    "CONFIRMADO_SIN_DATOS (cliente confirmó la compra "
                    "pero NO completó los datos de envío — usá esta tag "
                    "SIEMPRE en combo con `escalate_to_human"
                    "(reason_category=ORDER_PENDING_SHIPPING_DETAILS)` "
                    "para que un humano cierre la operación pidiendo los "
                    "datos faltantes; NO programa remarketing)."
                ),
            },
            "motivo": {
                "type": "string",
                "description": (
                    "Resumen breve, de máximo 2 líneas, de por qué se aplicó "
                    "esta etiqueta dado el contexto del chat."
                ),
                "minLength": 1,
            },
        },
        "required": ["tag", "motivo"],
    }

    def __init__(self, workspace: str | Path, vault_dir: str | Path | None = None):
        # POST-MORTEM workflow remarketing-wa_573125671604: el `workspace` que
        # llega aqui es el RUNTIME WORKSPACE CANONICO del agente, compartido
        # entre TODAS las sesiones — escribir `metadata.json` ahi pisa el
        # estado entre clientes distintos y NO es lo que `LoadOrStartSalesSession`
        # lee al rutear el siguiente webhook. El parametro queda por
        # compatibilidad pero NO se usa para metadata.
        # `vault_dir` (DI-friendly para tests): default = `WORKSPACE_VAULT_DIR`.
        self._workspace = Path(workspace)
        self._vault_dir = Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR

    async def execute_with_context(self, ctx: ToolContext, tag: str, motivo: str) -> str:
        # Path per-sesion (NO el workspace canonico del agente).
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))

        data["tag"] = tag
        data["motivo"] = motivo

        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": tag,
                "motivo": motivo,
                "active_route": data.get("active_route", ROUTE_VENTAS),
                "timestamp": time.time(),
            }
        )

        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # ADR-001: la tool no abre temporal_client. Si el tag indica seguimiento,
        # devuelve una decision serializada para que el workflow programe el remarketing.
        response: dict[str, Any] = {
            "message": f"Éxito. Interacción etiquetada como '{tag}'.",
        }
        if tag == "INTERESADO":
            response["schedule_remarketing"] = {
                "session_id": ctx.session_key,
                "motivo": motivo,
                "delay_seconds": 60,
            }
            response["message"] += " Se programó un ciclo de remarketing automáticamente."

        return json.dumps(response, ensure_ascii=False)
