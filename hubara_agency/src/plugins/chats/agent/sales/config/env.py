"""
config/env.py — plugin ``chats`` / sub-agente Sales.

Resuelve el path del workspace runtime del agente Sales. Este módulo es el
ÚNICO lugar dentro del sub-agente Sales (``src.plugins.chats.agent.sales``)
autorizado a leer ``os.environ`` para resolver paths; el resto recibe el
path por constructor injection (R-DIP).

Por qué per-sub-agente (``EXOCLAW_WORKSPACE_SALES``) en vez de un global
``EXOCLAW_WORKSPACE``:

    El plugin ``chats`` empaqueta dos sub-agentes (sales + remarketing) con
    distinta identidad, soul, tools y skills. Una sola env var global los
    forzaría a compartir workspace dir. Per-sub-agente queda independiente
    en producción (cada uno con su propio PVC).
    Ver ADR-2026-05-06-03 en ``agent_coordination/decisions.md``.

El path se cablea en un ``WorkspaceConfig(path=str(workspace))`` (boundary DTO)
que llega a ``bootstrap_sales_session_activity``. La activity llama a
``DefaultConversation.create`` con este path para que ``ContextBuilder``
encuentre los bootstrap files (IDENTITY.md, SOUL.md, USER.md, TOOLS.md,
AGENTS.md, más memory/* y skills/*).
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = `<repo>/hubara_agency/src/plugins/chats/agent/sales/workspace/`.
# Resolución mecánica desde __file__:
#   .parents[0] -> .../sales/config/
#   .parents[1] -> .../sales/
#   .parents[1] / "workspace" -> .../sales/workspace/
_DEFAULT_WORKSPACE = (Path(__file__).resolve().parents[1] / "workspace").resolve()


def get_workspace_path() -> Path:
    """Resolve el workspace runtime del sub-agente Sales del plugin chats.

    Default: ``<repo>/hubara_agency/src/plugins/chats/agent/sales/workspace/``
    (committed al repo para dev convenience).

    Override con ``EXOCLAW_WORKSPACE_SALES`` en producción apuntando a un
    volumen persistente para que ``memory/MEMORY.md`` y ``memory/HISTORY.md``
    sobrevivan a restarts del container.
    """
    raw = os.environ.get("EXOCLAW_WORKSPACE_SALES")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKSPACE
