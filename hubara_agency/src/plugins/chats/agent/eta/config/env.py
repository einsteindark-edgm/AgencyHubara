"""
config/env.py — plugin ``chats`` / sub-agente ETA.

Resuelve el path del workspace runtime del agente ETA. Este módulo es el ÚNICO
lugar dentro del sub-agente ETA (``src.plugins.chats.agent.eta``) autorizado a
leer ``os.environ`` para resolver paths; el resto recibe el path por constructor
injection (R-DIP).

Por qué per-sub-agente (``EXOCLAW_WORKSPACE_ETA``) en vez de un global
``EXOCLAW_WORKSPACE``: el plugin ``chats`` empaqueta tres sub-agentes (sales +
remarketing + eta) con distinta identidad, soul y tools. Una sola env var global
los forzaría a compartir workspace dir. Per-sub-agente queda independiente en
producción (cada uno con su propio PVC). Ver ADR-2026-05-06-03.

El path se cablea en un ``WorkspaceConfig(path=str(workspace))`` que llega a
``bootstrap_eta_session_activity``, donde ``ContextBuilder`` encuentra los
bootstrap files (IDENTITY.md, SOUL.md, USER.md, TOOLS.md, AGENTS.md, memory/*).
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = `<repo>/hubara_agency/src/plugins/chats/agent/eta/workspace/`.
#   .parents[0] -> .../eta/config/
#   .parents[1] -> .../eta/
#   .parents[1] / "workspace" -> .../eta/workspace/
_DEFAULT_WORKSPACE = (Path(__file__).resolve().parents[1] / "workspace").resolve()


def get_workspace_path() -> Path:
    """Resolve el workspace runtime del sub-agente ETA del plugin chats.

    Default: ``<repo>/hubara_agency/src/plugins/chats/agent/eta/workspace/``
    (committed al repo para dev convenience).

    Override con ``EXOCLAW_WORKSPACE_ETA`` en producción apuntando a un volumen
    persistente para que ``memory/MEMORY.md`` y ``memory/HISTORY.md`` sobrevivan
    a restarts del container.
    """
    raw = os.environ.get("EXOCLAW_WORKSPACE_ETA")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKSPACE
