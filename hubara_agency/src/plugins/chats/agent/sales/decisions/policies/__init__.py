"""Políticas del motor: convierten las respuestas de Jev en decisiones. PURAS y
versionadas por id (`turno-v1`…). Una política nueva entra con su test rojo
(respuestas falsas de Jev → decisión esperada) y un perfil que la use."""
from __future__ import annotations

from types import ModuleType

from src.plugins.chats.agent.sales.decisions.policies import turno_v1, turno_v2, turno_v3, turno_v4
from src.plugins.chats.agent.sales.decisions.retiro import en_retiro

_POLICIES: dict[str, ModuleType] = {
    turno_v1.POLICY_ID: turno_v1,
    turno_v2.POLICY_ID: turno_v2,
    turno_v3.POLICY_ID: turno_v3,
    turno_v4.POLICY_ID: turno_v4,
}


def get_policy(policy_id: str) -> ModuleType:
    try:
        return _POLICIES[policy_id]
    except KeyError:
        raise KeyError(f"política desconocida: {policy_id}") from None


@en_retiro("funcion:policy_ids")
def policy_ids() -> tuple[str, ...]:
    return tuple(sorted(_POLICIES))
