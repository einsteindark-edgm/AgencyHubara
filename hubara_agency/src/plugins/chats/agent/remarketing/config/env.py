"""
config/env.py — plugin ``chats`` / sub-agente Remarketing.

Resuelve el path del workspace runtime del agente Remarketing. Este módulo es
el ÚNICO lugar dentro del sub-agente Remarketing
(``src.plugins.chats.agent.remarketing``) autorizado a leer ``os.environ``
para resolver paths; el resto recibe el path por constructor injection (R-DIP).

Por qué per-sub-agente (``EXOCLAW_WORKSPACE_REMARKETING``) en vez de un
global ``EXOCLAW_WORKSPACE``:

    El plugin ``chats`` empaqueta dos sub-agentes (sales + remarketing) con
    distinta identidad, soul, tools y skills. Una sola env var global los
    forzaría a compartir workspace dir. Per-sub-agente queda independiente
    en producción (cada uno con su propio PVC).
    Ver ADR-2026-05-06-03 en ``agent_coordination/decisions.md``.

El path se cablea en un ``WorkspaceConfig(path=str(workspace))`` (boundary DTO)
que viaja (vía PR-A) por ``RemarketingSessionInput.runtime_workspace_path``
hasta ``bootstrap_remarketing_session_activity``. PR-B: la activity lo consume
e instancia ``WorkspaceConfig(path=runtime_workspace_path)`` directo —
``build_workspace_config(session_id)`` (el vault per-session) ya no se llama
para identidad/catálogo. El vault per-session sigue siendo dueño del JSONL
message store y los metadata files; el workspace dir es dueño de los bootstrap
files (IDENTITY.md, SOUL.md, USER.md, TOOLS.md, AGENTS.md, más memory/* y
skills/*).
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = `<repo>/hubara_agency/src/plugins/chats/agent/remarketing/workspace/`.
# Resolución mecánica desde __file__:
#   .parents[0] -> .../remarketing/config/
#   .parents[1] -> .../remarketing/
#   .parents[1] / "workspace" -> .../remarketing/workspace/
_DEFAULT_WORKSPACE = (Path(__file__).resolve().parents[1] / "workspace").resolve()


def get_workspace_path() -> Path:
    """Resolve el workspace runtime del sub-agente Remarketing del plugin chats.

    Default: ``<repo>/hubara_agency/src/plugins/chats/agent/remarketing/workspace/``
    (committed al repo para dev convenience).

    Override con ``EXOCLAW_WORKSPACE_REMARKETING`` en producción apuntando a
    un volumen persistente para que ``memory/MEMORY.md`` y ``memory/HISTORY.md``
    sobrevivan a restarts del container.
    """
    raw = os.environ.get("EXOCLAW_WORKSPACE_REMARKETING")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKSPACE
