"""Run ebbc203d (2026-09-16): el bot escribió el precio del anuncio ($45.000)
con el set a $49.500 en el catálogo. Ningún check lo cazaba: la verificación
comparó snapshot vs live (iguales) y el resumen salió a $49.500 sin
explicación. DES-10 audita los montos que el bot escribe contra el catálogo.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.engine import run_code_checks
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.verdict import compute_scorecard
from tests.evals.scorecard.incidents import (
    ebbc203d_after_fix,
    ebbc203d_before_fix,
    halloween_ctx,
)


def _failures(card) -> dict[str, int | None]:
    return {r["check_id"]: r["turn"] for r in card.results if r["verdict"] == "falla"}


def test_ebbc203d_before_fix_fails_des10_on_the_quote_turn() -> None:
    t = ebbc203d_before_fix()
    card = compute_scorecard(t, SPECS_BY_ID, run_code_checks(t, halloween_ctx()))
    assert card.verdict == "FALLA"
    assert _failures(card)["DES-10"] == 5


def test_ebbc203d_after_fix_passes_des10() -> None:
    t = ebbc203d_after_fix()
    results = run_code_checks(t, halloween_ctx())
    des10 = next(r for r in results if r.check_id == "DES-10")
    assert des10.verdict == "pasa"
