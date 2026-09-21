"""Guard único del ``session_id`` que llega por URL a los endpoints del dashboard.

Un ``session_id`` es el nombre de un directorio del vault
(``<vault>/<session_id>/...``). Si el segmento crudo de la URL llega a un
``Path`` sin validar, ``..`` lee el PADRE del vault, ``.`` el vault mismo y
``_analytics`` un directorio que no es una sesión (hallazgo de endurecimiento
2026-09-18 sobre ``GET /api/dashboard/sessions/{session_id}``).

El criterio es el piso anti-traversal de la plataforma
(``src.sdk.runtime.is_vault_session_id``, compartido con ``orders``): un
charset que no puede ni expresar un traversal, con tope de largo. Acá solo se
traduce a HTTP.

Las acciones que ESCRIBEN sobre un número real (``session_actions``,
``order_intake``) siguen con su regex más estricto (``wa_<8-15 dígitos>``):
este guard es el piso anti-traversal, no la política de formato de ellas.
"""
from __future__ import annotations

from fastapi import HTTPException

from src.sdk.runtime import is_vault_session_id


def require_valid_session_id(session_id: str) -> None:
    """400 si ``session_id`` no es un id de sesión del vault."""
    if not is_vault_session_id(session_id):
        raise HTTPException(status_code=400, detail="session_id inválido")
