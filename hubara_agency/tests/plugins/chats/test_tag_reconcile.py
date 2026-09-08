"""D1.3 — MBA propone la etiqueta, Hubara decide (`use_cases/tag_reconcile`).

Tabla propuesta × estado real de la sesión → etiqueta aplicada. Función pura
sobre el dict `metadata` (no toca filesystem): la consume `/session-actions/{s}/tag`.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.plugins.chats.agent.sales.use_cases.tag_reconcile import (
    ORDER_PENDING_SHIPPING_REASON,
    TagDecision,
    reconcile_tag_proposal,
)


def _episode(*, order_id: str | None = None, closed: bool = False, slots: dict[str, Any] | None = None) -> dict[str, Any]:
    ep: dict[str, Any] = {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": 2 if closed else None,
                          "closing_tag": "CONFIRMADO_PAGO_PENDIENTE" if closed else None, "order_id": order_id}
    if slots is not None:
        ep["order_draft"] = {"slots": slots, "updated_at_ms": 1}
    return ep


_SHIPPING = {"producto": "luz-serena", "ciudad": "Bogotá", "direccion": "Cl 1 # 2-3"}
_PRODUCT_ONLY = {"producto": "luz-serena", "color": "Blanco", "cantidad": "2"}

CASES: list[tuple[str, str, dict[str, Any], str, str, str, str | None]] = [
    # id, propuesta, metadata, → aplicada, acción, razón, escalación
    ("nueva-interesado", "INTERESADO", {}, "INTERESADO", "apply", "proposal_accepted", None),
    ("nueva-rechazo", "RECHAZO", {}, "RECHAZO", "apply", "proposal_accepted", None),
    ("solo-producto-interesado", "INTERESADO", {"episodes": [_episode(slots=_PRODUCT_ONLY)]},
     "INTERESADO", "apply", "proposal_accepted", None),
    ("solo-producto-rechazo", "RECHAZO", {"episodes": [_episode(slots=_PRODUCT_ONLY)]},
     "RECHAZO", "apply", "proposal_accepted", None),
    ("datos-envio-sin-orden-interesado", "INTERESADO", {"episodes": [_episode(slots=_SHIPPING)]},
     "CONFIRMADO_SIN_DATOS", "apply", "shipping_data_without_order", ORDER_PENDING_SHIPPING_REASON),
    ("datos-envio-sin-orden-rechazo", "RECHAZO", {"episodes": [_episode(slots=_SHIPPING)]},
     "CONFIRMADO_SIN_DATOS", "apply", "shipping_data_without_order", ORDER_PENDING_SHIPPING_REASON),
    ("metodo-pago-sin-orden", "INTERESADO", {"episodes": [_episode(slots={"producto": "x", "metodo_pago": "anticipado"})]},
     "CONFIRMADO_SIN_DATOS", "apply", "shipping_data_without_order", ORDER_PENDING_SHIPPING_REASON),
    ("orden-en-episodio-cerrado", "INTERESADO",
     {"tag": "CONFIRMADO_PAGO_PENDIENTE", "episodes": [_episode(order_id="order_1", closed=True, slots=_SHIPPING)]},
     "CONFIRMADO_PAGO_PENDIENTE", "discard", "order_registered", None),
    ("orden-verificada-por-humano", "RECHAZO",
     {"tag": "COMPRA_EXITOSA", "episodes": [_episode(order_id="order_1", closed=True)]},
     "COMPRA_EXITOSA", "discard", "order_registered", None),
    ("orden-en-episodio-activo", "INTERESADO",
     {"tag": "NO_ETIQUETADO", "episodes": [_episode(order_id="order_1", slots=_SHIPPING)]},
     "NO_ETIQUETADO", "discard", "order_registered", None),
    ("legacy-sin-episodios-con-orden", "RECHAZO",
     {"tag": "CONFIRMADO_PAGO_PENDIENTE", "registered_order": {"success": True, "order_id": "order_9"}},
     "CONFIRMADO_PAGO_PENDIENTE", "discard", "order_registered", None),
    ("orden-vieja-episodio-nuevo-es-venta-nueva", "INTERESADO",
     {"tag": "NO_ETIQUETADO", "episodes": [_episode(order_id="order_1", closed=True), {**_episode(), "episode_id": "ep_002"}]},
     "INTERESADO", "apply", "proposal_accepted", None),
    ("ya-interesado", "INTERESADO", {"tag": "INTERESADO", "episodes": [_episode()]},
     "INTERESADO", "already", "already_applied", None),
    ("ya-rechazo", "RECHAZO", {"tag": "RECHAZO", "episodes": [{**_episode(closed=True), "closing_tag": "RECHAZO"}]},
     "RECHAZO", "already", "already_applied", None),
    # Episodio CERRADO con datos de envío: su closing_tag es la verdad; un draft
    # viejo no reescala ni reetiqueta como "faltan datos" (revisión D1.3 H1).
    ("rechazo-cerrado-con-datos-no-reescala", "INTERESADO",
     {"tag": "RECHAZO", "episodes": [{**_episode(closed=True, slots=_SHIPPING), "closing_tag": "RECHAZO"}]},
     "INTERESADO", "apply", "proposal_accepted", None),
    ("sin-datos-resuelto-por-colega-y-devuelto", "RECHAZO",
     {"tag": "RETOMA_VENTA", "episodes": [{**_episode(closed=True, slots=_SHIPPING), "closing_tag": "CONFIRMADO_SIN_DATOS"}]},
     "RECHAZO", "apply", "proposal_accepted", None),
    ("timeout-cerrado-con-datos", "INTERESADO",
     {"tag": "NO_ETIQUETADO", "episodes": [{**_episode(closed=True, slots=_SHIPPING), "closing_tag": "TIMEOUT"}]},
     "INTERESADO", "apply", "proposal_accepted", None),
]


@pytest.mark.parametrize("case_id,proposed,metadata,applied,action,reason,escalate", CASES, ids=[c[0] for c in CASES])
def test_reconciliation_table(case_id, proposed, metadata, applied, action, reason, escalate) -> None:
    before = repr(metadata)
    decision = reconcile_tag_proposal(metadata, proposed=proposed)
    assert isinstance(decision, TagDecision)
    assert (decision.applied, decision.action, decision.reason, decision.escalate_reason) == (applied, action, reason, escalate)
    assert decision.proposed == proposed
    assert repr(metadata) == before, "la reconciliación es pura: no muta metadata"


def test_only_the_two_proposals_mba_can_make_are_reconciled() -> None:
    with pytest.raises(ValueError):
        reconcile_tag_proposal({}, proposed="COMPRA_EXITOSA")


def test_the_shipping_reason_is_the_one_the_sales_safety_net_uses() -> None:
    """Misma categoría que el workflow Sales garantiza para CONFIRMADO_SIN_DATOS
    (`ensure_closing_escalation`): la bandeja humana filtra por ella."""
    from src.plugins.chats.agent.sales.workflows.sales_session import _CLOSING_TAGS_REQUIRING_ESCALATION

    assert _CLOSING_TAGS_REQUIRING_ESCALATION["CONFIRMADO_SIN_DATOS"] == ORDER_PENDING_SHIPPING_REASON
