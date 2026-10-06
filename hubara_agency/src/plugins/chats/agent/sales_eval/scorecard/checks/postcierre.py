"""Checks de código de la familia `postcierre` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    judged,
    not_applicable,
    not_judged,
    passed,
    quote,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_PAYMENT_CLAIM_RE = re.compile(
    r"\b(pago|pagaste|transferencia)\b[^.?!\n]{0,30}"
    r"\b(confirmad[oa]|recibid[oa]|aprobad[oa]|verificad[oa]|qued[oó] (listo|registrado))\b",
    re.IGNORECASE,
)
_REGISTERED_RE = re.compile(r"\bregistrad[oa]\b", re.IGNORECASE)


def _post_order_turns_trace(traj: Trajectory) -> list[Turn]:
    start = next((i for i, t in enumerate(traj.turns) if t.state.get("order_id")), None)
    picked = set(range(start, len(traj.turns))) if start is not None else set()
    if traj.order_id:
        picked |= {i for i, t in enumerate(traj.turns) if t.stage_in == "postcierre"}
    return [traj.turns[i] for i in sorted(picked)]


@code_check("POS-01")
def check_payment_claimed_only_when_verified(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    legacy = is_legacy(traj)
    if legacy:
        if not traj.order_id:
            return not_applicable("POS-01", "el episodio no tiene orden")
        start = next(
            (i for i, t in enumerate(traj.turns) if any(_REGISTERED_RE.search(x) for x in t.sent_texts)), None
        )
        if start is None:
            return unknown("POS-01", "legacy sin forma de ubicar el turno del registro")
        scope = list(traj.turns[start:])
    else:
        if not traj.order_id and not any(t.state.get("order_id") for t in traj.turns):
            return not_applicable("POS-01", "el episodio no tiene orden")
        scope = _post_order_turns_trace(traj)
    scope = [t for t in scope if t.sent_texts and judged(traj, t)]
    if not scope:
        return not_judged("POS-01", traj, "sin textos después de registrar la orden")
    for turn in scope:
        for text in turn.sent_texts:
            if not _PAYMENT_CLAIM_RE.search(text):
                continue
            if turn.tool_ok("check_order_status"):
                continue
            if legacy and turn.tool_attempted("check_order_status"):
                return unknown("POS-01", f"turno {turn.turn}: legacy sin resultado de check_order_status")
            return failed(
                "POS-01", turn.turn, f"turno {turn.turn}: afirma pago sin verificarlo en el turno {quote(text)}"
            )
    return passed("POS-01")
