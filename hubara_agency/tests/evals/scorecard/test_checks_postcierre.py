"""Checks de la familia `postcierre` (HU-SC-1): pago afirmado solo con pago
verificado en el turno."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_ORDER_STATE = {"tag": "CONFIRMADO_PAGO_PENDIENTE", "route": "ventas", "order_id": "order_01", "changes": []}


def _run(t):
    return CODE_CHECKS["POS-01"](t, CheckContext())


def _registered(*after):
    return traj(
        T(1, stage_in="cierre", sent=["¡Tu pedido quedó registrado!"], tools=[tool("register_order")],
          state=_ORDER_STATE),
        *after,
    )


def test_pos01_payment_claim_with_paid_status_passes() -> None:
    t = _registered(
        T(2, stage_in="postcierre", inbound="¿ya les llegó?", state=_ORDER_STATE,
          sent=["Sí, tu pago quedó confirmado 🤍"], tools=[tool("check_order_status", notes=["pay_status:paid"])]),
    )
    r = _run(t)
    assert r.check_id == "POS-01"
    assert r.verdict == "pasa"


def test_pos01_payment_claim_without_status_check_fails_on_that_turn() -> None:
    t = _registered(
        T(2, stage_in="postcierre", inbound="ya pagué", state=_ORDER_STATE, sent=["Gracias, estamos revisando"]),
        T(3, stage_in="postcierre", inbound="¿y?", state=_ORDER_STATE, sent=["¡Listo! Tu pago fue recibido"]),
    )
    r = _run(t)
    assert (r.verdict, r.turn) == ("falla", 3)
    assert "pago fue recibido" in r.evidence


def test_pos01_post_order_text_without_claim_passes() -> None:
    t = _registered(T(2, stage_in="postcierre", state=_ORDER_STATE, sent=["En cuanto verifiquemos te aviso"]))
    assert _run(t).verdict == "pasa"


def test_pos01_postcierre_stage_with_episode_order_counts() -> None:
    t = traj(
        T(1, stage_in="postcierre", sent=["Tu transferencia quedó registrada y aprobada"]),
        order_id="order_01",
    )
    assert (_run(t).verdict, _run(t).turn) == ("falla", 1)


def test_pos01_claim_without_order_is_not_applicable() -> None:
    t = traj(T(1, sent=["Tu pago fue recibido"]))
    assert _run(t).verdict == "no_aplica"


def test_pos01_legacy_claim_after_registered_text_fails() -> None:
    t = traj(
        T(1, sent=["¡Tu pedido quedó registrado!"], stage_in=None),
        T(2, sent=["Tu pago fue confirmado"], stage_in=None),
        fidelity="legacy",
        order_id="order_01",
    )
    assert (_run(t).verdict, _run(t).turn) == ("falla", 2)


def test_pos01_legacy_without_registered_text_is_unknown() -> None:
    t = traj(T(1, sent=["Tu pago fue confirmado"], stage_in=None), fidelity="legacy", order_id="order_01")
    assert _run(t).verdict == "desconocido"
