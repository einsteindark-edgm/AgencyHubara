"""
Actividad Customizada: execute_tool

Esta actividad ES OBLIGATORIA para reemplazar la nativa de exoclaw_temporal.
La actividad nativa tiene hardcodeados sus registros puros. Esta versión híbrida invoca nuesto propio
src.core.registries, lo que permite que el Agente use Herramientas Personalizadas creadas por la Agencia.
"""

from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
from temporalio import activity

from src.domains.sales_whatsapp import integrations as whatsapp_client

from exoclaw.agent.tools.protocol import ToolContext
from exoclaw_temporal.config import ExecuteToolInput

from src.core.registries import get_base_tools_registry

@activity.defn(name="execute_tool")
async def execute_tool(input: ExecuteToolInput) -> str:
    """Implementación agnóstica de ejecución de herramientas con control de pulsos (Heartbeat) de Temporal."""
    
    # Cargamos el registro híbrido (Base Exoclaw + Clientes Agencia)
    registry = get_base_tools_registry(Path(input.workspace.path))
    
    ctx = ToolContext(
        session_key=input.session_id,
        channel=input.channel,
        chat_id=input.chat_id,
    )

    # El Heartbeat mantiene viva la conexión para que scripts lentos no sean reiniciados por Temporal
    async def _heartbeat_loop() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(10)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        if input.params is None:
            input.params = {}
        input.params["ctx"] = ctx
        return await registry.execute(input.name, input.params, ctx)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

@activity.defn(name="claim_conversation_routing")
async def claim_conversation_routing(workspace_path: str, new_route: str) -> None:
    vault = Path(workspace_path)
    metadata_file = vault / "metadata.json"
    data = {}
    if metadata_file.exists():
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    
    data["active_route"] = new_route
    metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

@activity.defn(name="send_whatsapp_message_activity")
async def send_whatsapp_message_activity(session_id: str, message: str) -> None:
    from src.core.config import WORKSPACE_VAULT_DIR
    import json
    import os
    
    # Extract phone from wa_12345
    from_number = session_id.replace("wa_", "")
    
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "TESTING")
    
    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
    except Exception:
        pass
    
    chunks = [chunk.strip() for chunk in message.split("\n\n") if chunk.strip()]
    for chunk in chunks:
        await whatsapp_client.send_message(phone_number_id, from_number, chunk)
        await asyncio.sleep(1.5)

@activity.defn(name="read_workspace_memory_activity")
async def read_workspace_memory_activity(session_id: str) -> str:
    """Lee de forma determinista y segura el memory.md del PVC para inyectarlo como contexto extra en un Workflow."""
    from src.core.config import WORKSPACE_VAULT_DIR
    
    memory_file = WORKSPACE_VAULT_DIR / session_id / "memory.md"
    try:
        if memory_file.exists():
            content = memory_file.read_text(encoding="utf-8").strip()
            if content:
                return f"\n\nRESUMEN MEMORIA DE LA VENTA PASADA (MUY IMPORTANTE LEER):\n{content}\n"
    except Exception:
        pass
    return ""
