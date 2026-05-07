"""Mecanismo de extension de tools por dominio (cierra NEW-5).

Antes (`src/core/activities.py:execute_tool`):
    from src.domains.sales_whatsapp.tools.routing import TransferToSalesAgentTool
    registry.register(TransferToSalesAgentTool(workspace=...))
Eso era una inversion DIP: `core` importaba `domain` por path concreto.

Ahora cada dominio registra su factory en el boot del Worker (composition
root legitimo, conoce ambos lados):
    register_tool_extension(
        "sales.transfer", lambda ws: TransferToSalesAgentTool(workspace=str(ws))
    )

Y el adapter `DefaultToolRegistry` (o `execute_tool` directo) llama
`apply_tool_extensions(registry, workspace_path)` que itera por las factories
registradas y las aplica al registry recien construido.

# R-STATELESS note: el dict `_EXTENSIONS` es modulo-level pero **inmutable
# despues del boot del worker**. Es metadata de registracion, no estado mutable
# de runtime. Cumple con la excepcion explicita en
# `deha-architecture/anti-patterns.md` para registries inmutables.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from exoclaw.agent.tools import Tool
    from exoclaw.agent.tools.registry import ToolRegistry


# key -> factory(workspace_path: Path) -> Tool
ToolFactory = Callable[[Path], "Tool"]
_EXTENSIONS: dict[str, ToolFactory] = {}


def register_tool_extension(key: str, factory: ToolFactory) -> None:
    """Registra una factory de Tool. Idempotente por `key` (overwrite OK).

    Llamado desde el `worker.py` de cada dominio antes de `Worker(...)`.
    """
    _EXTENSIONS[key] = factory


def apply_tool_extensions(registry: "ToolRegistry", workspace_path: Path) -> None:
    """Aplica todas las extensions registradas al `registry` para `workspace_path`."""
    for factory in _EXTENSIONS.values():
        registry.register(factory(workspace_path))


def clear_tool_extensions() -> None:
    """Limpia todas las extensions. Solo para uso en tests."""
    _EXTENSIONS.clear()
