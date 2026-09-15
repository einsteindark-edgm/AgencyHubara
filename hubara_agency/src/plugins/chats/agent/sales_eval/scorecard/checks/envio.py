"""Checks de código de la familia `envio` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re

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
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_SHIPPING_FLOW = "shipping_flow"
_FORM_DATA_MARKER = "[datos de envío recibidos]"

_SHIPPING_FIELDS: dict[str, re.Pattern[str]] = {
    "ciudad": re.compile(r"\bciudad\b", re.IGNORECASE),
    "direccion": re.compile(r"\bdirecci[oó]n\b", re.IGNORECASE),
    "telefono": re.compile(r"\btel[eé]fono\b|\bcelular\b", re.IGNORECASE),
    "nombre_recibe": re.compile(r"\bqui[eé]n recibe\b|\bnombre de (la persona|quien)\b", re.IGNORECASE),
}

# Dato de pago = medio o cuenta seguido (o precedido) de cerca por un número de
# 6+ dígitos. Nombrar el medio ("puedes pagar por Nequi") es legítimo: el guion
# pide informar las formas de pago; lo prohibido es escribir cuentas o llaves
# (los datos bancarios los manda el sistema).
# 6+ dígitos seguidos (con espacios o guiones opcionales), sin separador de
# miles ni signo de pesos: una cuenta o llave, no un precio ("$89.000").
_ACCOUNT_NUMBER = r"(?<![\d$.,])(?<!\$ )\d(?:[ -]?\d){5,}(?![\d]|[.,]\d)"
_BANK_DATA_RE = re.compile(
    r"\b(nequi|daviplata|bancolombia|llave|n[uú]mero de cuenta|cuenta de ahorros|cuenta corriente)\b"
    r"[^\n]{0,40}?" + _ACCOUNT_NUMBER
    + r"|" + _ACCOUNT_NUMBER + r"[^\n]{0,20}?\b(nequi|daviplata|bancolombia|llave)\b",
    re.IGNORECASE,
)
_SHIPPING_VALUE_RE = re.compile(
    r"\benv[ií]o\b[^.?!\n]{0,40}\b(cuesta|vale|es de|sale en|tiene un (costo|valor) de)\b[^.?!\n]{0,15}\$\s?\d",
    re.IGNORECASE,
)


def _form_turns(traj: Trajectory) -> list[Turn]:
    return [t for t in traj.turns if _SHIPPING_FLOW in t.intents]


@code_check("ENV-01")
def check_shipping_form_once(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    forms = _form_turns(traj)
    if not forms:
        return not_applicable("ENV-01", "no se envió el formulario de envío")
    if len(forms) >= 2:
        first, second = forms[0], forms[1]
        return failed(
            "ENV-01", second.turn, f"turno {second.turn}: formulario repetido (ya salió en el turno {first.turn})"
        )
    return passed("ENV-01", f"formulario en el turno {forms[0].turn}", turn=forms[0].turn)


def _answers_written_message(turn: Turn) -> bool:
    if turn.trigger == "handoff":
        return True
    return turn.trigger == "customer" and not turn.inbound_text.startswith("[")


@code_check("ENV-02")
def check_form_with_reply(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    applicable = [t for t in _form_turns(traj) if _answers_written_message(t)]
    if not applicable:
        return not_applicable("ENV-02", "ningún formulario respondió a un mensaje escrito del cliente")
    for turn in applicable:
        if not turn.sent_texts:
            detail = f"; narración descartada {quote(turn.discarded_narration[0])}" if turn.discarded_narration else ""
            return failed(
                "ENV-02", turn.turn,
                f"turno {turn.turn}: formulario sin texto ante {quote(turn.inbound_text)}{detail}",
            )
    return passed("ENV-02")


@code_check("ENV-03")
def check_form_data_recorded(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    arrivals = [t for t in traj.turns if t.inbound_text.lower().startswith(_FORM_DATA_MARKER)]
    if not arrivals:
        return not_applicable("ENV-03", "el cliente no envió el formulario")
    legacy = is_legacy(traj)
    for turn in arrivals:
        recorded = turn.tool_attempted("set_order_slot") if legacy else turn.tool_ok("set_order_slot")
        if not recorded:
            return failed(
                "ENV-03", turn.turn, f"turno {turn.turn}: llegaron los datos de envío y no quedaron con set_order_slot"
            )
    return passed("ENV-03")


def _known_fields(draft: dict | None) -> list[str]:
    return [f for f in _SHIPPING_FIELDS if (draft or {}).get(f)]


@code_check("ENV-04")
def check_no_reask_shipping_data(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("ENV-04", "legacy sin borrador del pedido")
    if not any(_known_fields(t.draft) for t in traj.turns):
        return not_applicable("ENV-04", "el pedido nunca tuvo datos de envío")
    for previous, turn in zip(traj.turns, traj.turns[1:], strict=False):
        known = _known_fields(previous.draft)
        for text in turn.sent_texts:
            if "?" not in text:
                continue
            for field_name in known:
                if _SHIPPING_FIELDS[field_name].search(text):
                    return failed(
                        "ENV-04", turn.turn,
                        f"turno {turn.turn}: vuelve a pedir {field_name}, ya capturado {quote(text)}",
                    )
    return passed("ENV-04")


@code_check("ENV-05")
def check_no_bank_data_or_shipping_value(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        if m := _BANK_DATA_RE.search(text):
            return failed(
                "ENV-05", turn.turn, f"turno {turn.turn}: dato de pago en texto «{m.group(0)}» {quote(text)}"
            )
        if _SHIPPING_VALUE_RE.search(text):
            return failed("ENV-05", turn.turn, f"turno {turn.turn}: valor de envío definitivo {quote(text)}")
    if not any_text:
        return not_applicable("ENV-05", "el bot no envió texto")
    return passed("ENV-05")


@code_check("ENV-07")
def check_receiver_name_before_register(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    attempts = [(t, tc) for t in traj.turns for tc in t.tools_named("register_order")]
    if not attempts:
        return not_applicable("ENV-07", "no se intentó registrar la orden")
    if is_legacy(traj) or all(tc.ok is None for _, tc in attempts):
        return unknown("ENV-07", "sin resultado de register_order")
    for turn, tc in attempts:
        if tc.error == "missing_receiver_name":
            return failed("ENV-07", turn.turn, f"turno {turn.turn}: register_order rechazado por missing_receiver_name")
    return passed("ENV-07")
