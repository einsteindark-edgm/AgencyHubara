"""CIE-03 y CIE-04 en el modo turno de producción (2026-10-07).

En modo turno las dos solo podían dar «pasa» o «sin señal»: la orden, la
etiqueta de pago pendiente y la escalación se juntan entre turnos. Una orden
registrada que se quedó sin su etiqueta, o sin la escalación de verificación,
nunca salía como FALLA. Con el episodio cerrado ya no hay nada que esperar:
se juzgan con el episodio completo y la falla queda en el turno que la causó.
"""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj


def _orphan_order(*, closed: bool):
    real = traj(
        T(1, sent=["¡Buenos días! Bienvenido a *Hubara*"], tools=[tool("send_quick_replies")]),
        T(2, inbound="Sí, regístralo", signal="affirmation", confirmed=True,
          tools=[tool("register_order", order_id="ord_1")], sent=["Listo"]),
        closing_tag="INTERESADO",
        order_id="ord_1",
    )
    return replace(real, closed_at_ms=10 * 60_000 if closed else None)


def _turn_result(record: dict, turn: int, check_id: str) -> str:
    rows = next(t for t in record["by_turn"] if t["turn"] == turn)["results"]
    return next(r["verdict"] for r in rows if r["check_id"] == check_id)


def test_a_closed_episode_with_an_orphan_order_fails_on_the_registration_turn() -> None:
    record = service.score_episode_turns(_orphan_order(closed=True), CheckContext(), states={})

    assert (_turn_result(record, 2, "CIE-03"), _turn_result(record, 2, "CIE-04")) == ("falla", "falla")
    assert next(t for t in record["by_turn"] if t["turn"] == 2)["verdict"] == "FALLA"
    assert record["verdict"] == "FALLA"


def test_an_open_episode_still_waits_for_the_tag_and_the_escalation() -> None:
    record = service.score_episode_turns(_orphan_order(closed=False), CheckContext(), states={})

    assert (_turn_result(record, 2, "CIE-03"), _turn_result(record, 2, "CIE-04")) == ("sin_senal", "sin_senal")
