"""Turnos que son SOLO un traspaso de remarketing (2026-10-07).

Desde que la ráfaga se junta en `coalesce_inbox`, la traza no marca el
traspaso en `trigger`: el turno llega como «customer» con el encuadre de la
plataforma como `inbound_text` y `first_contact` en True si el historial no
traía mensajes del agente de ventas. Leído así, APE-01 exigía el saludo de un
primer contacto que el bot omite a propósito y DES-03 leía el resumen y las
instrucciones como si las hubiera escrito el cliente.
"""
from __future__ import annotations

from src.platform.workflow_helpers import _handoff_takeover_framing
from src.plugins.chats.agent.sales_eval.scorecard.engine import run_code_checks
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import build_trajectory
from tests.evals.scorecard.test_trajectory import _trace


def _verdicts(summary: str, **over) -> dict[str, str]:
    traj = build_trajectory(
        [_trace(1, inbound_text=_handoff_takeover_framing(summary), first_contact=True, **over)],
        session_id="wa_100000000001",
        episode={"episode_id": "ep_007"},
    )
    return {r.check_id: r.verdict for r in run_code_checks(traj, CheckContext())}


def test_a_handoff_is_not_a_first_contact_so_the_greeting_is_not_required() -> None:
    v = _verdicts("Usuario respondió: Hola, quiero el de lavanda", sent_texts=["Claro, te muestro el de lavanda"])
    assert (v["APE-01"], v["APE-02"], v["APE-03"]) == ("no_aplica", "no_aplica", "no_aplica")


def test_a_handoff_asks_for_the_catalog_only_if_the_customer_did() -> None:
    assert _verdicts("Usuario respondió: Muchas gracias")["DES-03"] == "no_aplica"
    asked = _verdicts("Usuario respondió: quiero ver el catálogo")
    assert asked["DES-03"] == "falla"  # pidió el catálogo y el turno no lo mostró
