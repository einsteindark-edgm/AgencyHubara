"""Helpers compartidos por los checks de código."""
from __future__ import annotations

import re
from collections.abc import Iterator

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_EVIDENCE_MAX = 280

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
    for t in traj.turns:
        for text in t.sent_texts:
            yield t, text


def quote(text: str, limit: int = 120) -> str:
    return f"«{clip(text, limit)}»"
