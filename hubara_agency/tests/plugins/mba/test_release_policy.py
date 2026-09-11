"""D1.6 — política de `release` (tabla pura del roadmap §D1.6).

Cuándo Hubara devuelve el hilo a Meta Business Agent después de un envío
proactivo propio. Precondición de todo: el hilo es nuestro (`control_owner
== hubara`, D1.5); con MBA al frente no hay nada que soltar.
"""

from __future__ import annotations

import pytest

from src.plugins.mba.domain.release_policy import (
    ReleaseDecision,
    ReleaseFacts,
    ReleaseTrigger,
    decide_release,
)

HUBARA = ReleaseFacts(control_owner="hubara")


@pytest.mark.parametrize(
    "trigger,facts,expected",
    [
        # Remarketing (INTERESADO) y el cliente responde: sí antes del pedido, no con orden registrada
        (
            ReleaseTrigger.REMARKETING_REPLY,
            HUBARA,
            ReleaseDecision(True, "remarketing_reply"),
        ),
        (
            ReleaseTrigger.REMARKETING_REPLY,
            ReleaseFacts("hubara", order_registered=True),
            ReleaseDecision(False, "order_in_progress"),
        ),
        # ETA / aviso de despacho: sí (D1.9 `agent_event` es mejor porque no toma el hilo)
        (ReleaseTrigger.ETA_NOTICE, HUBARA, ReleaseDecision(True, "eta_notice")),
        (
            ReleaseTrigger.ETA_NOTICE,
            ReleaseFacts("hubara", order_registered=True),
            ReleaseDecision(True, "eta_notice"),
        ),
        # Handoff resuelto por el operador desde el inbox: sí
        (
            ReleaseTrigger.HANDOFF_RESOLVED,
            HUBARA,
            ReleaseDecision(True, "handoff_resolved"),
        ),
        (
            ReleaseTrigger.HANDOFF_RESOLVED,
            ReleaseFacts("hubara", order_registered=True),
            ReleaseDecision(True, "handoff_resolved"),
        ),
        # Comprobante verificado: no hasta emitir `agent_event` payment_received; luego sí
        (
            ReleaseTrigger.RECEIPT_VERIFIED,
            HUBARA,
            ReleaseDecision(False, "await_agent_event"),
        ),
        (
            ReleaseTrigger.RECEIPT_VERIFIED,
            ReleaseFacts("hubara", agent_event_emitted=True),
            ReleaseDecision(True, "receipt_verified"),
        ),
        # Manual (operador): sí
        (ReleaseTrigger.MANUAL, HUBARA, ReleaseDecision(True, "manual")),
    ],
)
def test_release_policy_table(
    trigger: ReleaseTrigger, facts: ReleaseFacts, expected: ReleaseDecision
) -> None:
    assert decide_release(trigger, facts) == expected


@pytest.mark.parametrize("trigger", list(ReleaseTrigger))
def test_nothing_is_released_while_mba_already_controls_the_thread(
    trigger: ReleaseTrigger,
) -> None:
    assert decide_release(
        trigger, ReleaseFacts(control_owner="mba")
    ) == ReleaseDecision(False, "already_mba")


@pytest.mark.parametrize(
    "trigger", [t for t in ReleaseTrigger if t is not ReleaseTrigger.MANUAL]
)
def test_an_unknown_owner_blocks_automatic_releases_but_not_the_operator(
    trigger: ReleaseTrigger,
) -> None:
    """Sin `messaging_handovers` nunca recibido no sabemos si el hilo es
    nuestro: un release ciego devuelve 4xx de Meta. El operador puede forzar."""
    assert decide_release(trigger, ReleaseFacts(control_owner=None)) == ReleaseDecision(
        False, "owner_unknown"
    )
    assert decide_release(
        ReleaseTrigger.MANUAL, ReleaseFacts(control_owner=None)
    ) == ReleaseDecision(True, "manual")


@pytest.mark.parametrize(
    "trigger", [t for t in ReleaseTrigger if t is not ReleaseTrigger.MANUAL]
)
def test_a_release_already_requested_and_not_yet_confirmed_by_meta_is_not_repeated_automatically(
    trigger: ReleaseTrigger,
) -> None:
    facts = ReleaseFacts(
        control_owner="hubara", release_pending=True, agent_event_emitted=True
    )
    assert decide_release(trigger, facts) == ReleaseDecision(False, "release_pending")


def test_the_operator_can_always_release_unless_mba_already_has_the_thread() -> None:
    """La salida cuando Meta no confirmó (webhook caído, hilo que no era
    nuestro): el manual no queda bloqueado por `release_pending` ni por
    `owner_unknown`; solo por `already_mba`."""
    assert decide_release(
        ReleaseTrigger.MANUAL, ReleaseFacts("hubara", release_pending=True)
    ) == ReleaseDecision(True, "manual")
    assert decide_release(
        ReleaseTrigger.MANUAL, ReleaseFacts(None, release_pending=True)
    ) == ReleaseDecision(True, "manual")
    assert decide_release(
        ReleaseTrigger.MANUAL, ReleaseFacts("mba", release_pending=True)
    ) == ReleaseDecision(False, "already_mba")


def test_triggers_are_stable_strings_for_the_http_contract() -> None:
    assert {t.value for t in ReleaseTrigger} == {
        "manual",
        "handoff_resolved",
        "remarketing_reply",
        "eta_notice",
        "receipt_verified",
    }
    assert ReleaseTrigger("manual") is ReleaseTrigger.MANUAL
