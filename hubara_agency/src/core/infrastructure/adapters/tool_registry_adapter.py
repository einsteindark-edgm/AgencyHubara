"""Adapter default para `ToolRegistryPort`.

Delega en las funciones de `src.core.registries`, y consume el mecanismo de
extension `tool_extensions` para permitir que dominios externos agreguen sus
propias tools sin que `core/registries.py` tenga que importarlas (rompe la
inversion DIP que vivia en `core/activities.py:execute_tool`).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.core.registries import (
    build_default_llm_config,
    build_workspace_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.core.tool_extensions import apply_tool_extensions

if TYPE_CHECKING:  # pragma: no cover
    from exoclaw.agent.tools.registry import ToolRegistry
    from exoclaw_temporal.config import LLMConfig, WorkspaceConfig


class DefaultToolRegistry:
    """Wrapper de las funciones modulo-level de `src.core.registries`.

    Satisface `ToolRegistryPort` con `@runtime_checkable`. La unica diferencia
    funcional vs llamar directo a las funciones es que `get_base_tools_registry`
    aplica las extensions registradas (NEW-5: la registracion de
    `TransferToSalesAgentTool` ya no se hace por import inline en `execute_tool`).
    """

    def build_default_llm_config(self) -> "LLMConfig":
        return build_default_llm_config()

    def build_workspace_config(self, session_id: str) -> "WorkspaceConfig":
        return build_workspace_config(session_id)

    def get_base_tools_registry(self, workspace_path: Path) -> "ToolRegistry":
        registry = get_base_tools_registry(workspace_path)
        apply_tool_extensions(registry, workspace_path)
        return registry

    def get_base_tools_json(self, registry: "ToolRegistry") -> str:
        return get_base_tools_json(registry)
