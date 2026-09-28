"""Políticas del motor: convierten las respuestas de Jev en decisiones. PURAS y
versionadas por id (`turno-v1`…). Una política nueva entra con su test rojo
(respuestas falsas de Jev → decisión esperada) y un perfil que la use."""
from __future__ import annotations

from types import ModuleType

from src.plugins.chats.agent.sales.decisions.policies import turno_v1, turno_v2

_POLICIES: dict[str, ModuleType] = {turno_v1.POLICY_ID: turno_v1, turno_v2.POLICY_ID: turno_v2}


def get_policy(policy_id: str) -> ModuleType:
    try:
        return _POLICIES[policy_id]
    except KeyError:
        raise KeyError(f"política desconocida: {policy_id}") from None


def policy_ids() -> tuple[str, ...]:
    return tuple(sorted(_POLICIES))
