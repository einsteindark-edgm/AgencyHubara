import json
import time
from pathlib import Path
from pydantic import Field
from exoclaw.agent.tools import Tool, ToolContext

from src.core.constants import ROUTE_VENTAS


class TransferToSalesAgentTool(Tool):
    """Herramienta estricta para el Agente de Remarketing: Úsala SOLO cuando el usuario responde a tu mensaje de reactivación y debes pasarle la conversación al agente de Ventas principal."""

    name = "transfer_to_sales_agent"
    description = "Transfiere el control de la conversación al Agente de Ventas devolviendo la sesión."

    resumen: str = Field(..., description="Breve resumen de 1 sola línea sobre lo que dijo el usuario para contextualizar al agente de ventas.")

    workspace: str = Field("", description="Internal: Do not provide", exclude=True)

    def __init__(self, workspace: str, **kwargs):
        super().__init__(**kwargs)
        self.workspace = workspace

    async def execute(self, ctx: ToolContext = None, resumen: str = None, **kwargs) -> str:
        _resumen = resumen if isinstance(resumen, str) else kwargs.get('resumen', 'El cliente volvió a interactuar')

        vault = Path(self.workspace)
        metadata_file = vault / "metadata.json"

        data = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))

        data["active_route"] = ROUTE_VENTAS
        data["tag"] = "RETOMA_VENTA"
        data["motivo"] = _resumen

        if "status_history" not in data:
            data["status_history"] = []

        data["status_history"].append({
            "tag": "RETOMA_VENTA",
            "motivo": _resumen,
            "active_route": ROUTE_VENTAS,
            "timestamp": time.time()
        })

        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # ADR-001: la tool ya NO abre temporal_client ni hace start_workflow.
        # Devuelve una decision serializada; el workflow la lee y dispara la activity.
        session_id = ctx.session_key if ctx else kwargs.get("session_id", vault.name)
        decision_payload = {
            "transfer_decision": {
                "session_id": session_id,
                "target_route": ROUTE_VENTAS,
                "summary": _resumen,
            },
            "message": "El control ha sido transferido. NO generes más texto, responde vacío o con 'Ok' para finalizar.",
        }
        return json.dumps(decision_payload, ensure_ascii=False)
