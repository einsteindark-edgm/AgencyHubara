"""Enrutador del workflow de ventas para la plataforma (motor de decisiones,
F2/F7).

La plataforma arranca Ventas por nombre desde el dispatcher de orquestación
(remarketing → ventas) y desde la activity de arranque. Qué versión arranca
(V1 o V2) lo decide el registro de bots por conversación; este módulo se lo
dice a la plataforma. Lo registran los workers que despachan hacia Ventas.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session


def route_sales_workflow(session_id: str) -> str:
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    return bot_for_session(session_id, vault_dir=Path(WORKSPACE_VAULT_DIR)).workflow


def register_sales_workflow_router() -> None:
    from src.sdk.foundation import register_workflow_router

    register_workflow_router("chats", "sales", route_sales_workflow)
