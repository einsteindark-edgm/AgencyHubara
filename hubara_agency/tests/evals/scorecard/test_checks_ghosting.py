"""Checks de ghosting (GHO-*)."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

CTX = CheckContext()


def run(cid, t):
    return CODE_CHECKS[cid](t, CTX)


def test_gho01_ghost_turn_that_writes_to_customer_fails() -> None:
    t = traj(T(1), T(2, trigger="ghost", sent=["Etiquetada como INTERESADO"]))
    assert (run("GHO-01", t).verdict, run("GHO-01", t).turn) == ("falla", 2)


def test_gho01_silent_ghost_passes_and_no_ghost_is_na() -> None:
    assert run("GHO-01", traj(T(1), T(2, trigger="ghost"))).verdict == "pasa"
    assert run("GHO-01", traj(T(1))).verdict == "no_aplica"
    assert run("GHO-01", traj(T(1), fidelity="legacy")).verdict == "desconocido"


def test_gho02_ghost_five_minutes_after_form_fails() -> None:
    t = traj(T(1, tools=[tool("request_shipping_details")], at_ms=1_000_000),
             T(2, trigger="ghost", at_ms=1_300_000))
    assert (run("GHO-02", t).verdict, run("GHO-02", t).turn) == ("falla", 2)


def test_gho02_ghost_ten_minutes_after_form_passes_and_reply_in_between_is_na() -> None:
    ok = traj(T(1, tools=[tool("request_shipping_details")], at_ms=1_000_000),
              T(2, trigger="ghost", at_ms=1_620_000))
    assert run("GHO-02", ok).verdict == "pasa"
    replied = traj(T(1, tools=[tool("request_shipping_details")]), T(2, inbound="[datos de envío recibidos]"))
    assert run("GHO-02", replied).verdict == "no_aplica"
