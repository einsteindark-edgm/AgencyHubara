"""Tool: TransferToSalesAgentTool.

DEHA-compliant tool that satisfies the exoclaw `Tool` Protocol via the
`ToolBase` mixin. Implements `execute_with_context(ctx, **params)` so that the
`ToolRegistry.execute` dispatch (`exoclaw.agent.tools.registry:102-105`) can
inject the `ToolContext` without callers having to smuggle it through `params`.

The tool is **inert** w.r.t. Temporal: it writes the decision into the per-session
`metadata.json` and returns a JSON envelope (`transfer_decision` + `message`) that
`core/workflow_helpers.py:_try_parse_decision_payload` parses and turns into a
`TransferDecision` dataclass. The workflow then issues the dispatcher activity
(ADR-001). The tool itself never imports `temporal_client`, never calls
`start_workflow`, never imports any workflow.

Inputs are validated by `ToolBase.validate_params` against the JSON schema below
(no Pydantic). The schema enforces `resumen: string, minLength=1`, so silent
defaults like the legacy `or 'El cliente volvió a interactuar'` are gone:
invalid params surface to the LLM as `Error: Invalid parameters ...`.

LOCATION RATIONALE:
This tool used to live in src/sales_whatsapp/tools/ but is consumed by the
remarketing_whatsapp agent (it transfers FROM remarketing back TO sales).
Living inside sales/ created a cross-agent import (remarketing → sales) which
violates R-DIP #10 (agents-independent). Moved to src/platform/tools/ so that
both sales and remarketing import it via the platform without coupling to
each other.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_VENTAS


class TransferToSalesAgentTool(ToolBase):
    """Transfer the conversation back to the Sales agent.

    Used **only** by the Remarketing agent when the user replies to a reactivation
    message and shows clear intent to resume the purchase. The tool serializes a
    `transfer_decision` envelope; the workflow consumes it via the dispatcher
    activity (ADR-001) — this tool never touches Temporal directly.
    """

    name = "transfer_to_sales_agent"
    description = (
        "Transfiere el control de la conversación al Agente de Ventas. "
        "Usar SOLO cuando el usuario responde a un mensaje de reactivación de "
        "remarketing y muestra intención clara de retomar la compra."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "resumen": {
                "type": "string",
                "description": (
                    "Briefing para que ventas retome SIN re-preguntar. DEBE "
                    "incluir, en este orden: (1) el texto LITERAL del último "
                    "mensaje del cliente entre comillas, (2) qué eligió ya el "
                    "cliente (producto, aroma, color, cantidad — lo que esté "
                    "confirmado en el historial), (3) cuál es el SIGUIENTE "
                    "dato pendiente del pedido. Ejemplo: 'Cliente escribió "
                    "\"Dame 2\". Ya eligió: Cruz de Vida, aroma Drakar, color "
                    "Gris, cantidad 2. Siguiente paso: datos de envío.'"
                ),
                "minLength": 1,
            },
        },
        "required": ["resumen"],
    }

    def __init__(self, workspace: str | Path, vault_dir: str | Path | None = None):
        # Mismo POST-MORTEM que ManageConversationTagTool: el `workspace` que
        # llega es el RUNTIME WORKSPACE CANONICO compartido — no usado para
        # metadata. `vault_dir` (DI-friendly): default = `WORKSPACE_VAULT_DIR`.
        self._workspace = Path(workspace)
        self._vault_dir = Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR

    async def execute_with_context(self, ctx: ToolContext, resumen: str) -> str:
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))

        data["active_route"] = ROUTE_VENTAS
        data["tag"] = "RETOMA_VENTA"
        data["motivo"] = resumen

        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": "RETOMA_VENTA",
                "motivo": resumen,
                "active_route": ROUTE_VENTAS,
                "timestamp": time.time(),
            }
        )

        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # ADR-001: la tool ya NO abre temporal_client ni hace start_workflow.
        # Devuelve una decision serializada; el workflow la lee y dispara la activity.
        decision_payload = {
            "transfer_decision": {
                "session_id": ctx.session_key,
                "target_route": ROUTE_VENTAS,
                "summary": resumen,
            },
            "message": (
                "El control ha sido transferido. NO generes más texto, "
                "responde vacío o con 'Ok' para finalizar."
            ),
        }
        return json.dumps(decision_payload, ensure_ascii=False)
