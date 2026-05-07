"""Boundary DTOs del dominio Remarketing.

Aplicacion de R-JSON: cualquier valor que cruce `workflow.execute_workflow` /
`workflow.run` es un dataclass plano JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RemarketingSessionInput:
    """Input del `RemarketingSessionWorkflow`.

    Campos:
      * `session_id`: identificador de la conversacion (`{WHATSAPP_SESSION_PREFIX}{phone}`).
      * `motivo`: el motivo registrado por Sales cuando la conversacion quedo
        en `INTERESADO` (e.g. "el cliente dudo del precio"). Usado aguas abajo
        en `build_remarketing_trigger_activity` para personalizar el gancho.
      * `runtime_workspace_path`: ruta del workspace canonico del agente de
        Remarketing (donde viven `IDENTITY.md`, `SOUL.md`, `USER.md`,
        `TOOLS.md`, `AGENTS.md`, `memory/*` y `skills/*`). Resuelto en el
        composition root (dispatcher / interfaz) via
        `config/env.py:get_workspace_path()` y propagado como string para
        cumplir R-JSON. PR-B: la activity ahora lo consume — instancia
        `WorkspaceConfig(path=runtime_workspace_path)` y falla fast con
        `RuntimeError` si falta (analogo a sales_whatsapp PR-B /
        ADR-2026-05-06-04). El campo permanece `Optional` solo por
        compatibilidad de DTOs en flight; en produccion el dispatcher SIEMPRE
        debe wirearlo.

    NOTA: el orden de los campos importa para replay. `session_id` y `motivo`
    eran los unicos campos pre-PR-A; `runtime_workspace_path` se agrega al
    final con default `None` para no romper fixtures viejas.
    """

    session_id: str
    motivo: str
    runtime_workspace_path: str | None = None
