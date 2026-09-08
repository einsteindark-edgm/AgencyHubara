"""D1.6 — política de ``release`` (pura): cuándo Hubara devuelve el hilo a
Meta Business Agent tras un envío proactivo propio.

Tabla del roadmap §D1.6:

| Envío proactivo de Hubara                     | ¿Release después?                                   |
|-----------------------------------------------|-----------------------------------------------------|
| Remarketing (INTERESADO) y el cliente responde| Sí antes del pedido; no con orden registrada         |
| ETA / aviso de despacho                       | Sí (D1.9 ``agent_event`` es mejor: no toma el hilo) |
| Handoff a humano resuelto por el operador     | Sí, al cerrar el caso desde el inbox                 |
| Comprobante verificado                        | No hasta emitir ``agent_event`` payment_received     |
| Manual (operador)                             | Sí                                                   |

Precondiciones, en orden: con ``control_owner == mba`` no hay nada que soltar
(``already_mba``); un release ya pedido y no confirmado por Meta no se repite
(``release_pending``); sin dueño conocido (Meta nunca avisó) los releases
automáticos NO se hacen (``owner_unknown``: soltar a ciegas es un 4xx de Meta),
solo el manual del operador.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["ReleaseDecision", "ReleaseFacts", "ReleaseTrigger", "decide_release"]

#: Mismos literales que ``src.sdk.runtime.CONTROL_OWNER_*`` (el test lo verifica).
_MBA = "mba"


class ReleaseTrigger(str, Enum):
    MANUAL = "manual"
    HANDOFF_RESOLVED = "handoff_resolved"
    REMARKETING_REPLY = "remarketing_reply"
    ETA_NOTICE = "eta_notice"
    RECEIPT_VERIFIED = "receipt_verified"


@dataclass(frozen=True)
class ReleaseFacts:
    control_owner: str | None = None  # mba | hubara | None (Meta nunca avisó)
    order_registered: bool = False
    agent_event_emitted: bool = False
    release_pending: bool = False  # release pedido y aún sin messaging_handovers de vuelta


@dataclass(frozen=True)
class ReleaseDecision:
    release: bool
    reason: str


def decide_release(trigger: ReleaseTrigger, facts: ReleaseFacts) -> ReleaseDecision:
    if facts.control_owner == _MBA:
        return ReleaseDecision(False, "already_mba")
    if facts.release_pending:
        return ReleaseDecision(False, "release_pending")
    if facts.control_owner is None and trigger is not ReleaseTrigger.MANUAL:
        return ReleaseDecision(False, "owner_unknown")
    if trigger is ReleaseTrigger.REMARKETING_REPLY and facts.order_registered:
        return ReleaseDecision(False, "order_in_progress")
    if trigger is ReleaseTrigger.RECEIPT_VERIFIED and not facts.agent_event_emitted:
        return ReleaseDecision(False, "await_agent_event")
    return ReleaseDecision(True, trigger.value)
