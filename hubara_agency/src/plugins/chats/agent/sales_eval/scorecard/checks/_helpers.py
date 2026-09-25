"""Helpers compartidos por los checks de código."""
from __future__ import annotations

import re
from collections.abc import Iterator

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_EVIDENCE_MAX = 280
SIN_SENAL = "sin_senal"

# Precio en pesos colombianos: "$89.000", "$ 45000", "89.000 COP", "45.000 pesos".
PRICE_RE = re.compile(
    r"\$\s?\d{1,3}(?:[.,]\d{3})+|\$\s?\d{4,}|\b\d{1,3}(?:[.,]\d{3})+\s?(?:cop|pesos)\b",
    re.IGNORECASE,
)


def clip(text: str, limit: int = _EVIDENCE_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def passed(check_id: str, evidence: str = "", turn: int | None = None) -> CheckResult:
    return CheckResult(check_id, "pasa", turn=turn, evidence=clip(evidence))


def failed(check_id: str, turn: int | None, evidence: str) -> CheckResult:
    return CheckResult(check_id, "falla", turn=turn, evidence=clip(evidence))


def not_applicable(check_id: str, reason: str = "") -> CheckResult:
    return CheckResult(check_id, "no_aplica", evidence=clip(reason))


def unknown(check_id: str, reason: str) -> CheckResult:
    return CheckResult(check_id, "desconocido", evidence=clip(reason))


def is_legacy(traj: Trajectory) -> bool:
    return traj.fidelity != "trace"


def sent_texts(traj: Trajectory) -> Iterator[tuple[Turn, str]]:
    """Textos enviados en los turnos que se juzgan (todos en modo episodio;
    solo el turno foco en modo turno)."""
    for t in traj.turns:
        if not judged(traj, t):
            continue
        for text in t.sent_texts:
            yield t, text


def all_sent_texts(traj: Trajectory) -> Iterator[tuple[Turn, str]]:
    """Todos los textos, prefijo incluido (p. ej. "el primer texto del episodio")."""
    for t in traj.turns:
        for text in t.sent_texts:
            yield t, text


def quote(text: str, limit: int = 120) -> str:
    return f"«{clip(text, limit)}»"


# ── Modo turno (laboratorio, plan §5.2) ─────────────────────────────────────
# `traj.focus_turn` = el turno que se juzga; los anteriores son contexto: los
# acumuladores (preguntas contadas, búsquedas hechas, datos conocidos) los
# recorren, pero solo el turno foco puede fallar. En modo episodio
# (`focus_turn is None`) todos los turnos se juzgan: nada cambia.


def in_focus(traj: Trajectory) -> bool:
    return traj.focus_turn is not None


def judged(traj: Trajectory, turn: Turn) -> bool:
    return traj.focus_turn is None or turn.turn == traj.focus_turn


def judged_turns(traj: Trajectory) -> list[Turn]:
    return [t for t in traj.turns if judged(traj, t)]


def focus_turn_of(traj: Trajectory) -> Turn | None:
    return next((t for t in traj.turns if t.turn == traj.focus_turn), None)


def not_judged(check_id: str, traj: Trajectory, reason: str) -> CheckResult:
    """`no_aplica` con el motivo de siempre; en modo turno dice que es del turno foco."""
    if traj.focus_turn is None:
        return not_applicable(check_id, reason)
    return not_applicable(check_id, f"{reason} (turno foco {traj.focus_turn})")


def no_signal(check_id: str, reason: str) -> CheckResult:
    """Sin señal en el turno foco: el check depende de turnos posteriores
    (cierre, pedido registrado). No es `pasa` ni `no_aplica`."""
    return CheckResult(check_id, SIN_SENAL, evidence=clip(reason))
