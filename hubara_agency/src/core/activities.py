"""
Actividad Customizada: execute_tool

Esta actividad ES OBLIGATORIA para reemplazar la nativa de exoclaw_temporal.
La actividad nativa tiene hardcodeados sus registros puros. Esta versión híbrida invoca nuesto propio
src.core.registries, lo que permite que el Agente use Herramientas Personalizadas creadas por la Agencia.
"""

from __future__ import annotations
import asyncio
import contextlib
from pathlib import Path
from temporalio import activity

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
        return await registry.execute(input.name, input.params, ctx)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
