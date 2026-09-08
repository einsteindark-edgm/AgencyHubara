"""Manifest de `orders`: las transiciones `OrderStageChangedEvent` → ETA.

Cambio de diseño 2026-09-08 (runs 01a07cd2 / 01a07e9f de `eta-wa_573229041190`):
`to_stage == preparing` disparaba `start_workflow_with_replace`, que TERMINA la
sesión ETA viva del cliente antes de arrancar una nueva. Cada pedido nuevo de
un cliente con seguimiento en curso dejaba un run `Terminated` y abría una
ventana de carrera (un `preparing` del pedido B en los mismos segundos en que
el workflow enviaba una notificación del pedido A la cortaba sin reintento).

Como la sesión ETA es multi-pedido (el tracking vive en
`metadata.eta_tracking.orders` y `claim_eta_notification_activity` da de alta
pedidos sin entry), `preparing` va por `signal_with_start` igual que el resto
de los stages: si hay sesión viva la absorbe; si no, la arranca con el seed.

Complementa `tests/architecture/test_manifest_orchestration_consistency.py`
(estructura) — acá se fija el CONTENIDO de las 5 transiciones.
"""
from __future__ import annotations

import pytest

from src.platform.orchestration import envelope_for
from src.platform.plugin_manifest import get_transitions
from src.plugins.orders.shared.contracts.events import OrderStageChangedEvent

STAGES = ["preparing", "ready", "shipping", "delivered", "cancelled"]


def _matching(stage: str):
    transitions = get_transitions("orders", "reconcile")
    env = envelope_for(
        OrderStageChangedEvent(
            session_id="wa_573000000000",
            order_id="order_01TEST",
            to_stage=stage,
            occurred_at_ms=1_757_000_000_000,
        ),
        source_plugin="orders",
        source_worker="reconcile",
    )
    return [t for t in transitions if t.matches(env)]


def test_preparing_joins_the_live_eta_session_instead_of_replacing_it() -> None:
    matching = _matching("preparing")
    assert len(matching) == 1, f"Expected 1 transition match, got {len(matching)}"
    t = matching[0]
    assert t.action.via == "signal_with_start", (
        "`preparing` debe entrar por signal_with_start: con start_workflow_with_"
        "replace cada pedido nuevo TERMINA la sesión ETA viva del cliente (runs "
        "Terminated + carrera con una notificación en vuelo)"
    )
    assert t.action.signal_name == "notify_stage_change"
    assert t.action.target_plugin == "eta"
    assert t.action.target_worker == "eta"
    assert t.action.target_workflow == "HubaraEtaSessionWorkflow"
    assert t.action.workflow_id_template == "eta-{event.session_id}"


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_signals_with_start_the_same_eta_session(stage: str) -> None:
    matching = _matching(stage)
    assert len(matching) == 1, f"{stage}: expected 1 match, got {len(matching)}"
    t = matching[0]
    assert t.action.via == "signal_with_start"
    assert t.action.signal_name == "notify_stage_change"
    assert t.action.workflow_id_template == "eta-{event.session_id}"


def test_no_order_stage_transition_terminates_the_eta_session() -> None:
    offenders = [
        t.id
        for t in get_transitions("orders", "reconcile")
        if t.on_event == "OrderStageChangedEvent"
        and t.action.via == "start_workflow_with_replace"
    ]
    assert offenders == [], f"transiciones que terminan la sesión ETA viva: {offenders}"
