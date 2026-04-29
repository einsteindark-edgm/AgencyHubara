"""
Actividad Customizada: execute_tool

Esta actividad ES OBLIGATORIA para reemplazar la nativa de exoclaw_temporal.
La actividad nativa tiene hardcodeados sus registros puros. Esta versión híbrida invoca nuesto propio
src.core.registries, lo que permite que el Agente use Herramientas Personalizadas creadas por la Agencia.
"""

from __future__ import annotations
import json
import time
from pathlib import Path
from temporalio import activity

from exoclaw.agent.tools.protocol import ToolContext
from exoclaw_temporal.config import ExecuteToolInput

from src.core.registries import get_base_tools_registry
from src.core.infrastructure.temporal.heartbeat import with_heartbeat
from src.core.config import WORKSPACE_VAULT_DIR

@activity.defn(name="execute_tool")
@with_heartbeat(every=10)
async def execute_tool(input: ExecuteToolInput) -> str:
    """Implementación agnóstica de ejecución de herramientas con control de pulsos (Heartbeat) de Temporal."""

    registry = get_base_tools_registry(Path(input.workspace.path))
    from src.domains.sales_whatsapp.tools.routing import TransferToSalesAgentTool
    registry.register(TransferToSalesAgentTool(workspace=str(input.workspace.path)))

    ctx = ToolContext(
        session_key=input.session_id,
        channel=input.channel,
        chat_id=input.chat_id,
    )

    if input.params is None:
        input.params = {}
    input.params["ctx"] = ctx
    return await registry.execute(input.name, input.params, ctx)

@activity.defn(name="claim_conversation_routing")
async def claim_conversation_routing(workspace_path: str, new_route: str) -> None:
    vault = Path(workspace_path)
    metadata_file = vault / "metadata.json"
    data = {}
    if metadata_file.exists():
        data = json.loads(metadata_file.read_text(encoding="utf-8"))

    data["active_route"] = new_route

    if "status_history" not in data:
        data["status_history"] = []

    data["status_history"].append({
        "tag": data.get("tag", "NO_ETIQUETADO"),
        "motivo": data.get("motivo", f"Transferencia de ruta a {new_route}"),
        "active_route": new_route,
        "timestamp": time.time()
    })

    metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

@activity.defn(name="read_workspace_memory_activity")
async def read_workspace_memory_activity(session_id: str) -> str:
    """Lee de forma determinista y segura el memory.md del PVC para inyectarlo como contexto extra en un Workflow."""
    memory_file = WORKSPACE_VAULT_DIR / session_id / "memory.md"
    try:
        if memory_file.exists():
            content = memory_file.read_text(encoding="utf-8").strip()
            if content:
                return f"\n\nRESUMEN MEMORIA DE LA VENTA PASADA (MUY IMPORTANTE LEER):\n{content}\n"
    except OSError:
        pass
    return ""
