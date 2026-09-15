"""Checks de código de la familia `ghosting` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    not_applicable,
    passed,
    quote,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory

_FORM_IDLE_MS = 600_000


@code_check("GHO-01")
def gho_01(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("GHO-01", "sin trazas: los turnos de ghosting no quedan en el historial")
    ghosts = [t for t in traj.turns if t.is_ghost]
    if not ghosts:
        return not_applicable("GHO-01", "sin turno de ghosting")
    for t in ghosts:
        if t.sent_texts:
            return failed("GHO-01", t.turn, f"turno {t.turn}: el ghosting le escribió al cliente {quote(t.sent_texts[0])}")
    return passed("GHO-01", f"{len(ghosts)} turno(s) de ghosting en silencio")


@code_check("GHO-02")
def gho_02(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("GHO-02", "sin trazas: no hay tiempos de turno")
    pairs = [
        (form, nxt)
        for form, nxt in zip(traj.turns, traj.turns[1:])
        if "shipping_flow" in form.intents and nxt.is_ghost
    ]
    if not pairs:
        return not_applicable("GHO-02", "sin ghosting inmediatamente después del formulario")
    for form, ghost in pairs:
        if form.at_ms is None or ghost.at_ms is None:
            return unknown("GHO-02", "turnos sin marca de tiempo")
        waited = ghost.at_ms - form.at_ms
        if waited < _FORM_IDLE_MS:
            return failed(
                "GHO-02", ghost.turn,
                f"turno {ghost.turn}: ghosting a los {waited // 1000} s del formulario (mínimo 600 s)",
            )
    return passed("GHO-02", "el ghosting respetó la espera del formulario")
