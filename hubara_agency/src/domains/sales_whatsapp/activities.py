"""Activities especificas del dominio Sales.

Aqui viven los "puentes" entre el workflow y los policies de `domain/policies/`. El
proposito es mantener los workflows finos (driving adapters) y permitir que los
prompts evolucionen sin tocar la shape de history (la activity stub se mantiene).
"""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from exoclaw_temporal.config import SessionInput

from src.core.registries import (
    build_default_llm_config,
    build_workspace_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.domains.sales_whatsapp.domain.policies.prompts import build_ghosting_prompt


@activity.defn(name="decide_ghosting_action")
async def decide_ghosting_action() -> str:
    """Devuelve el prompt inyectado cuando se detecta ghosting.

    No tiene side effects ni I/O. Existe como activity (no como llamada directa
    en el workflow) para mantener los strings de negocio fuera del workflow code.
    """
    return build_ghosting_prompt()


@activity.defn(name="bootstrap_sales_session_activity")
async def bootstrap_sales_session_activity(session_id: str) -> SessionInput:
    """Construye el `SessionInput` JSON-safe del agente de Sales.

    Saca del @workflow.run la I/O de filesystem (`build_workspace_config` hace
    `Path.mkdir`) y la construccion del registry de tools. Replica el patron
    aplicado a Remarketing (PR F6.1) — los callers (service.py, dispatcher
    activities) ya no construyen `SessionInput` antes de `start_workflow`,
    solo pasan `SalesSessionInput(session_id=...)` y el bootstrap se ejecuta
    como primera activity dentro del workflow (R-DET / R-JSON).

    Inputs son primitivos (str). El output es un dataclass plano
    JSON-serializable que cruza la frontera del workflow (R-JSON).
    """
    llm = build_default_llm_config()
    ws = build_workspace_config(session_id)  # mkdir OK aqui: estamos dentro de activity
    registry = get_base_tools_registry(Path(ws.path))

    return SessionInput(
        session_id=session_id,
        channel="whatsapp",
        chat_id=session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )
