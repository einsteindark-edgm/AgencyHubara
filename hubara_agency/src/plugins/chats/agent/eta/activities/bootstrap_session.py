"""Activity de bootstrap del sub-agente ETA.

Construye el ``SessionInput`` JSON-safe dentro del workflow (R-DET / R-JSON),
espejo de ``bootstrap_remarketing_session_activity`` /
``bootstrap_sales_session_activity``. Saca del ``@workflow.run`` la I/O de
filesystem (lectura del workspace) y la construcción del registry de tools.
"""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from exoclaw_temporal.config import SessionInput, WorkspaceConfig

from src.platform.registries import (
    build_default_llm_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.platform.tool_extensions import apply_tool_extensions
from src.plugins.chats.agent.eta.contracts import EtaSessionInput


@activity.defn(name="bootstrap_eta_session_activity")
async def bootstrap_eta_session_activity(input: EtaSessionInput) -> SessionInput:
    """Construye el ``SessionInput`` del agente ETA.

    ``input.runtime_workspace_path`` llega ``None`` cuando el caller es el
    dispatcher declarativo (no conoce el config de un sibling agent — sería
    R-DIP #10). En ese caso resolvemos el path localmente vía
    ``config/env.py:get_workspace_path()`` — config del propio agente, sin leak
    cross-agent. Es el patrón idéntico a Sales/Remarketing.
    """
    runtime_path = input.runtime_workspace_path
    if not runtime_path:
        from src.plugins.chats.agent.eta.config.env import get_workspace_path

        runtime_path = str(get_workspace_path())
        activity.logger.info(
            "bootstrap_eta_session_activity: runtime_workspace_path missing on "
            "input (declarative dispatcher) — falling back to local config: %s",
            runtime_path,
        )

    llm = build_default_llm_config()
    ws = WorkspaceConfig(path=runtime_path)
    activity.logger.info("bootstrap_eta_session_activity: workspace=%s", runtime_path)

    # El registry de tools apunta al workspace canónico. POST-MORTEM (mismo que
    # Sales/Remarketing): aplicar las tool extensions ANTES de generar el
    # `tool_definitions_json`, sino las tools registradas en `workers/eta.py`
    # (`register_tool_extension`) viajan vacías y el LLM no sabe que existen.
    registry = get_base_tools_registry(Path(ws.path))
    apply_tool_extensions(registry, Path(ws.path))

    return SessionInput(
        session_id=input.session_id,
        channel="whatsapp",
        chat_id=input.session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )
