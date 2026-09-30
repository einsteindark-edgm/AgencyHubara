"""Checks de código de la familia `estilo` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re
from typing import Any

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
    judged_turns,
    not_judged,
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
    return passed("EST-01") if any_text else not_judged("EST-01", traj, _NO_TEXT)


@code_check("EST-02")
def check_dash_and_emojis(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if problem := _emoji_problem(text):
            return failed("EST-02", turn.turn, f"turno {turn.turn}: {problem} {quote(text)}")
    return passed("EST-02") if any_text else not_judged("EST-02", traj, _NO_TEXT)


@code_check("EST-03")
def check_no_admin_text(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if looks_like_admin_leak(text):
            return failed("EST-03", turn.turn, f"turno {turn.turn}: texto administrativo al cliente {quote(text)}")
    return passed("EST-03") if any_text else not_judged("EST-03", traj, _NO_TEXT)


@code_check("EST-03b")
def check_admin_guard_acted(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("EST-03b", "legacy sin guardas")
    for turn in judged_turns(traj):
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
    return passed("EST-05") if any_text else not_judged("EST-05", traj, _NO_TEXT)


@code_check("EST-06")
def check_no_discarded_narration(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("EST-06", "legacy sin narración descartada")
    for turn in judged_turns(traj):
        if turn.discarded_narration:
            return failed(
                "EST-06", turn.turn, f"turno {turn.turn}: narración descartada {quote(turn.discarded_narration[0])}"
            )
    return passed("EST-06")


# ── EST-09 · cortesía sin empujón de venta (operador, 2026-09-30) ─────────
# El evaluador lee el mensaje con reglas propias (no con la lectura del bot que
# juzga): gracias, elogios o un saludo, sin preguntas de verdad ni pedidos.
_COURTESY_RE = re.compile(
    r"\b(gracias|agradezco|agradecid[ao]s?|geniales?|hermos[ao]s?|lind[ao]s?|bell[ao]s?|precios[ao]s?|"
    r"me encant\w*|nos encant\w*|perfect[ao]s?|excelentes?|chever\w*|qu[eé] bien|qu[eé] bueno|"
    r"bendicion(es)?|felicidades|s[uú]per)\b|[🙏❤😍🥰💪👍🤍💕😊]",
    re.IGNORECASE,
)
_REQUEST_RE = re.compile(
    r"\b(quiero|quisiera|necesito|me (mandas|env[ií]as|regalas)|cu[aá]nto|precios?|vale|cuesta|env[ií]os?|"
    r"domicilio|cu[aá]ndo|d[oó]nde|c[oó]mo (pago|hago)|cat[aá]logo|tienen|hay|otr[ao]s?|puedo|pueden|podr[ií]an)\b",
    re.IGNORECASE,
)
_GREETING_QUESTION_RE = re.compile(r"¿?\s*(c[oó]mo (est[aá]n|est[aá]s|vas|van)|qu[eé] tal)\s*\?", re.IGNORECASE)
#: Las notas que el ingest antepone al mensaje («[Conversación anterior…]»,
#: «[El cliente responde a este mensaje que le enviamos: «…»]»); pueden
#: ocupar varias líneas si el aviso citado las tiene.
_INGEST_NOTE_RE = re.compile(r"^\s*\[[^\]]*\]\s*\n")
_HANDOFF_REPLY = "Usuario respondió:"
_SALES_PUSH_RE = re.compile(
    r"(en qu[eé] (m[aá]s )?(te|le|les) (puedo|podemos) ayudar"
    r"|(te|le) (puedo )?ayud(o|ar|amos)? con algo m[aá]s"
    r"|algo m[aá]s en (lo )?que (te|le) (pueda|podamos) ayudar"
    r"|(quieres|te gustar[ií]a|deseas) (ver|conocer|mirar) (el cat[aá]logo|m[aá]s|otr[oa]s|nuestr)"
    r"|buscas algo (m[aá]s|en especial|en particular)"
    r"|qu[eé] (te gustar[ií]a|necesitas|est[aá]s buscando|buscas)\b)",
    re.IGNORECASE,
)
_CATALOG_INTENTS = frozenset({"quick_replies", "products_list", "product_gallery", "categories"})


def _without_ingest_notes(text: str) -> str:
    while m := _INGEST_NOTE_RE.match(text):
        text = text[m.end():]
    return text


def _customer_words(turn: Any) -> str | None:
    """Lo que escribió el cliente en el turno, sin las notas del ingest (en la
    traza v1 y en cada mensaje de la v2); en un traspaso desde remarketing, lo
    que respondió al gancho."""
    if turn.trigger == "handoff":
        text = turn.inbound_text.strip()
        return text[len(_HANDOFF_REPLY):].strip() if text.startswith(_HANDOFF_REPLY) else None
    if turn.trigger != "customer":
        return None
    texts = [str(m.text or "") for m in turn.inbound] if turn.inbound else [turn.inbound_text]
    return " ".join(_without_ingest_notes(t).strip() for t in texts).strip()


def _is_courtesy(text: str) -> bool:
    body = _GREETING_QUESTION_RE.sub(" ", text)
    return (
        bool(_COURTESY_RE.search(body))
        and not _REQUEST_RE.search(body)
        and "?" not in body
        and len(body.split()) <= 30
    )


@code_check("EST-09")
def check_courtesy_without_sales_push(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    courtesy = [t for t in judged_turns(traj) if (words := _customer_words(t)) and _is_courtesy(words)]
    if not courtesy:
        return not_judged("EST-09", traj, "el cliente no respondió solo con una cortesía")
    for turn in courtesy:
        for text in turn.sent_texts:
            if m := _SALES_PUSH_RE.search(text):
                return failed("EST-09", turn.turn, f"turno {turn.turn}: «{m.group(0)}» a una cortesía {quote(text)}")
        if offered := sorted(set(turn.intents) & _CATALOG_INTENTS):
            return failed("EST-09", turn.turn, f"turno {turn.turn}: ofreció {', '.join(offered)} a una cortesía")
    return passed("EST-09")
