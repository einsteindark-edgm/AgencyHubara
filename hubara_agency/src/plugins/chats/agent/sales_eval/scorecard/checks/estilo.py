"""Checks de código de la familia `estilo` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.evals.script_rubric import (
    DASH_RE,
    VOSEO_RES,
    disallowed_emojis,
    find_emojis,
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
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory
from src.sdk.agentkit import looks_like_admin_leak

_NO_TEXT = "el bot no envió texto"

_DELIVERY_VERB_RE = re.compile(
    r"\b(te|le|les) (llega|llegar[aá]|entregamos|entregar[eé]mos)\b[^.?!\n]{0,30}"
    r"\b(en|el|este|ma[nñ]ana|hoy)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(\d+|un|una|dos|tres)\s*(d[ií]as?|horas?|semanas?)\b|\bma[nñ]ana\b|\bhoy\b", re.IGNORECASE
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+")


def _emoji_problem(text: str) -> str | None:
    if m := DASH_RE.search(text):
        return f"guion largo «{m.group(0)}»"
    if bad := disallowed_emojis(text):
        return f"emoji fuera de la lista {' '.join(bad)}"
    for paragraph in text.split("\n\n"):
        if len(emojis := find_emojis(paragraph)) > 1:
            return f"{len(emojis)} emojis en una burbuja {' '.join(emojis)}"
    return None


@code_check("EST-01")
def check_no_voseo(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        for rx in VOSEO_RES:
            if m := rx.search(text):
                return failed("EST-01", turn.turn, f"turno {turn.turn}: voseo «{m.group(0)}» {quote(text)}")
    return passed("EST-01") if any_text else not_applicable("EST-01", _NO_TEXT)


@code_check("EST-02")
def check_dash_and_emojis(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if problem := _emoji_problem(text):
            return failed("EST-02", turn.turn, f"turno {turn.turn}: {problem} {quote(text)}")
    return passed("EST-02") if any_text else not_applicable("EST-02", _NO_TEXT)


@code_check("EST-03")
def check_no_admin_text(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if looks_like_admin_leak(text):
            return failed("EST-03", turn.turn, f"turno {turn.turn}: texto administrativo al cliente {quote(text)}")
    return passed("EST-03") if any_text else not_applicable("EST-03", _NO_TEXT)


@code_check("EST-03b")
def check_admin_guard_acted(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("EST-03b", "legacy sin guardas")
    for turn in traj.turns:
        if "admin_text_guard" in turn.guards or turn.suppressed_reason == "admin_text_guard":
            detail = f" {quote(turn.llm_text)}" if turn.llm_text else ""
            return failed("EST-03b", turn.turn, f"turno {turn.turn}: la guarda bloqueó texto administrativo{detail}")
    return passed("EST-03b")


def _promises_delivery_time(text: str) -> bool:
    return any(
        _DELIVERY_VERB_RE.search(sentence) and _TIME_RE.search(sentence)
        for sentence in _SENTENCE_RE.findall(text)
    )


@code_check("EST-05")
def check_no_delivery_promises(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if _promises_delivery_time(text):
            return failed("EST-05", turn.turn, f"turno {turn.turn}: promete plazo de entrega {quote(text)}")
    return passed("EST-05") if any_text else not_applicable("EST-05", _NO_TEXT)


@code_check("EST-06")
def check_no_discarded_narration(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("EST-06", "legacy sin narración descartada")
    for turn in traj.turns:
        if turn.discarded_narration:
            return failed(
                "EST-06", turn.turn, f"turno {turn.turn}: narración descartada {quote(turn.discarded_narration[0])}"
            )
    return passed("EST-06")
