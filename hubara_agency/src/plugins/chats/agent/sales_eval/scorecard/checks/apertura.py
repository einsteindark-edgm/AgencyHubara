"""Checks de código de la familia `apertura` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.evals.script_rubric import (
    BRAND_RE,
    FORBIDDEN_OPENERS,
    GREETING_RE,
)
from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    not_applicable,
    passed,
    quote,
    sent_texts,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import (
    CATALOG_DISPLAY_INTENTS,
    Trajectory,
    Turn,
)

_WELCOME_RE = re.compile(r"bienvenid", re.IGNORECASE)
_OPENER_WINDOW = 30


def _first_contact_gate(check_id: str, traj: Trajectory) -> CheckResult | None:
    """`None` si es primer contacto; si no, el veredicto de aplicabilidad."""
    if traj.first_contact is None:
        return unknown(check_id, "la trayectoria no dice si era primer contacto")
    if traj.first_contact is False:
        return not_applicable(check_id, "no es primer contacto")
    return None


def _first_sent(traj: Trajectory) -> tuple[Turn, str] | None:
    return next(iter(sent_texts(traj)), None)


@code_check("APE-01")
def check_greeting_first_contact(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if (gate := _first_contact_gate("APE-01", traj)) is not None:
        return gate
    first = _first_sent(traj)
    if first is None:
        return failed("APE-01", traj.turns[0].turn, "no se envió saludo en el primer contacto")
    turn, text = first
    missing = [
        label
        for label, rx in (("saludo por hora", GREETING_RE), ("marca Hubara", BRAND_RE))
        if not rx.search(text)
    ]
    if missing:
        return failed(
            "APE-01", turn.turn, f"turno {turn.turn}: primer texto sin {' ni '.join(missing)} {quote(text)}"
        )
    return passed("APE-01", f"turno {turn.turn}: {quote(text)}", turn=turn.turn)


@code_check("APE-02")
def check_no_forbidden_opener(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if (gate := _first_contact_gate("APE-02", traj)) is not None:
        return gate
    first = _first_sent(traj)
    if first is None:
        return not_applicable("APE-02", "no se envió texto en el primer contacto")
    turn, text = first
    head = text[:_OPENER_WINDOW]
    for rx in FORBIDDEN_OPENERS:
        if m := rx.search(head):
            return failed(
                "APE-02", turn.turn, f"turno {turn.turn}: abre con «{m.group(0).strip()}» {quote(text)}"
            )
    return passed("APE-02", f"turno {turn.turn}: {quote(text)}", turn=turn.turn)


@code_check("APE-03")
def check_catalog_offered_at_opening(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if (gate := _first_contact_gate("APE-03", traj)) is not None:
        return gate
    opening = traj.turns[0]
    intents = set(opening.intents)
    if "quick_replies" in intents or intents & CATALOG_DISPLAY_INTENTS:
        return passed("APE-03", f"turno {opening.turn}: {', '.join(opening.intents)}", turn=opening.turn)
    if is_legacy(traj) and not intents:
        return unknown("APE-03", "legacy sin componentes en la apertura")
    return failed(
        "APE-03", opening.turn, f"turno {opening.turn}: la apertura no ofreció botones ni catálogo"
    )


@code_check("APE-04")
def check_no_regreeting(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if traj.first_contact is None:
        return unknown("APE-04", "la trayectoria no dice si era primer contacto")
    if traj.first_contact is True:
        return not_applicable("APE-04", "es primer contacto")
    for turn, text in sent_texts(traj):
        if _WELCOME_RE.search(text) and BRAND_RE.search(text):
            return failed("APE-04", turn.turn, f"turno {turn.turn}: vuelve a dar la bienvenida {quote(text)}")
    return passed("APE-04")
