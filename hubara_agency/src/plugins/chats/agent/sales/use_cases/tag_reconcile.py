"""Reconciliación de etiquetas propuestas por un agente externo (D1.3 MBA).

Meta Business Agent (plugin `mba`) NO decide el estado comercial de una
conversación: PROPONE `INTERESADO` o `RECHAZO` al despedirse y Hubara la
reconcilia con el estado real de la sesión antes de aplicarla. Las reglas
son deterministas y viven acá (chats es quien tiene el estado; P-3 impide que
`mba` lea el vault):

1. **Orden registrada gana.** Si el último episodio tiene `order_id` (o, en
   sesiones legacy sin `episodes[]`, `registered_order.success`), la
   propuesta se DESCARTA: un `INTERESADO` acá dispararía remarketing a un
   cliente que ya compró y un `RECHAZO` borraría una venta. El tag visible
   queda como está (`CONFIRMADO_PAGO_PENDIENTE` / `COMPRA_EXITOSA` / ...).
2. **Datos de envío sin orden = confirmó y no terminó.** Si el episodio
   ACTIVO tiene en el draft algún slot de la etapa de envío (ciudad,
   dirección, teléfono, quien recibe, cédula, método de pago) y no hay
   orden, se aplica `CONFIRMADO_SIN_DATOS` (cierra el episodio) y hay que
   escalar `ORDER_PENDING_SHIPPING_DETAILS`: exactamente lo que garantiza la
   red de seguridad `ensure_closing_escalation` del workflow Sales. Vale
   para las dos propuestas: en duda, un colega mira el caso antes de dar la
   venta por perdida (regla de oro del skill de escalación). Solo el
   episodio activo: uno ya cerrado (RECHAZO, CONFIRMADO_SIN_DATOS resuelto
   por el colega y devuelto al bot, TIMEOUT) tiene su `closing_tag` como
   verdad y no se reetiqueta ni se reescala por un draft viejo.
3. **Si no, la propuesta se aplica tal cual.** Con solo producto / color /
   cantidad elegidos no hay confirmación: `INTERESADO` sigue siendo el tag
   correcto (el Window Strategist decide el seguimiento). Sobre un episodio
   cerrado se comporta como la tool del agente Sales: cambia el tag visible
   sin reabrir ni re-cerrar el episodio.
4. **Idempotente por (sesión, tag).** Si el tag visible ya es el que se
   aplicaría, no se vuelve a escribir (ni historial ni evento).

DEHA: función pura sobre el dict `metadata` (NO lo muta; devuelve una
decisión). El caller (`api/session_actions.py`) aplica la decisión con las
tools/mutadores existentes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode

__all__ = [
    "ORDER_PENDING_SHIPPING_REASON",
    "PROPOSALS",
    "SHIPPING_STAGE_SLOTS",
    "TagDecision",
    "reconcile_tag_proposal",
]

#: Lo único que un agente externo puede proponer.
PROPOSALS: frozenset[str] = frozenset({"INTERESADO", "RECHAZO"})

#: Misma categoría que `ensure_closing_escalation` usa para CONFIRMADO_SIN_DATOS.
ORDER_PENDING_SHIPPING_REASON = "ORDER_PENDING_SHIPPING_DETAILS"

CONFIRMED_WITHOUT_DATA_TAG = "CONFIRMADO_SIN_DATOS"

#: Slots del draft que solo aparecen cuando el cliente ya decidió comprar
#: (etapa "datos de envío" del guion): su presencia sin orden = confirmó y
#: no terminó.
SHIPPING_STAGE_SLOTS: frozenset[str] = frozenset(
    {"ciudad", "barrio", "direccion", "telefono", "nombre_recibe", "cedula", "metodo_pago"}
)

Action = Literal["apply", "discard", "already"]
Reason = Literal["proposal_accepted", "order_registered", "shipping_data_without_order", "already_applied"]


@dataclass(frozen=True)
class TagDecision:
    proposed: str
    applied: str  # tag que queda visible tras la decisión
    action: Action
    reason: Reason
    escalate_reason: str | None = None  # categoría a escalar si la decisión lo exige

    @property
    def reconciled(self) -> bool:
        """True si Hubara decidió algo distinto de lo propuesto."""
        return self.applied != self.proposed


def _last_episode(metadata: dict[str, Any]) -> dict[str, Any] | None:
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return None
    last = episodes[-1]
    return last if isinstance(last, dict) else None


def _order_registered(metadata: dict[str, Any]) -> bool:
    last = _last_episode(metadata)
    if last is not None:
        return bool(last.get("order_id"))
    registered = metadata.get("registered_order")
    return isinstance(registered, dict) and registered.get("success") is True


def _shipping_data_present(metadata: dict[str, Any]) -> bool:
    """Slots de la etapa de envío en el episodio ACTIVO (un draft de un
    episodio ya cerrado no reabre nada: su `closing_tag` es la verdad)."""
    active = get_active_episode(metadata)
    if active is None:
        return False
    draft = active.get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) else None
    if not isinstance(slots, dict):
        return False
    return any(str(slots.get(key) or "").strip() for key in SHIPPING_STAGE_SLOTS)


def reconcile_tag_proposal(metadata: dict[str, Any], *, proposed: str) -> TagDecision:
    """Propuesta × estado real → decisión. Pura (no muta ``metadata``)."""
    if proposed not in PROPOSALS:
        raise ValueError(f"propuesta no reconciliable: {proposed!r} (solo {sorted(PROPOSALS)})")
    current = str(metadata.get("tag") or "")

    if _order_registered(metadata):
        return TagDecision(proposed=proposed, applied=current, action="discard", reason="order_registered")

    if _shipping_data_present(metadata):
        target, escalate = CONFIRMED_WITHOUT_DATA_TAG, ORDER_PENDING_SHIPPING_REASON
        reason: Reason = "shipping_data_without_order"
    else:
        target, escalate, reason = proposed, None, "proposal_accepted"

    if current == target:
        return TagDecision(proposed=proposed, applied=target, action="already", reason="already_applied")
    return TagDecision(proposed=proposed, applied=target, action="apply", reason=reason, escalate_reason=escalate)
