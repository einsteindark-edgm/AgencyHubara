"""Port: ToolRegistryPort.

Describe la capability de bootstrap del session vault: configuracion del LLM,
configuracion del workspace, y construccion del `ToolRegistry` de exoclaw.

Los activities de bootstrap (`bootstrap_sales_session_activity`,
`bootstrap_remarketing_session_activity`) y `execute_tool` consumen este puerto
para armar el `SessionInput` JSON-safe que cruza la frontera del workflow
(R-JSON).

El adapter default (`DefaultToolRegistry`) delega en las funciones existentes
de `src.core.registries`. Mantener separado el Protocol del impl permite a los
tests usar fakes que no dependen de `exoclaw.agent.tools.ToolRegistry`.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from exoclaw.agent.tools.registry import ToolRegistry
    from exoclaw_temporal.config import LLMConfig, WorkspaceConfig


@runtime_checkable
class ToolRegistryPort(Protocol):
    """Bootstrap del workspace, LLM y registry de tools por sesion."""

    def build_default_llm_config(self) -> "LLMConfig":
        """Devuelve la configuracion LLM por defecto (model, api_key, etc.)."""
        ...

    def build_workspace_config(self, session_id: str) -> "WorkspaceConfig":
        """Devuelve un WorkspaceConfig con `path` aislado por sesion.

        Crea el directorio si no existe (mkdir es OK aqui: el caller es una
        activity, no un workflow — R-DET no aplica a side effects de filesystem
        dentro de activities).
        """
        ...

    def get_base_tools_registry(self, workspace_path: Path) -> "ToolRegistry":
        """Construye el ToolRegistry con las tools base (filesystem + dominio).

        Implementaciones concretas pueden enchufar tools adicionales registradas
        a traves de un mecanismo de extension (vease `tool_extensions`).
        """
        ...

    def get_base_tools_json(self, registry: "ToolRegistry") -> str:
        """Serializa las definiciones de tools del registry a JSON determinista."""
        ...
