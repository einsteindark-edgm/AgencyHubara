"""Checks de código de la familia `envio` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re
import unicodedata

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    focus_turn_of,
    in_focus,
    is_legacy,
    judged,
    judged_turns,
    lost_narration,
    not_applicable,
    not_judged,
    passed,
    quote,
    sent_texts,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn
from src.plugins.chats.shared.purchase_signals import detect_deferral, detect_purchase_affirmation

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
    if in_focus(traj):
        focus = focus_turn_of(traj)
        if focus is None or focus.turn not in {f.turn for f in forms}:
            return not_judged("ENV-01", traj, "no se envió el formulario de envío")
        if forms[0].turn != focus.turn:
            return failed(
                "ENV-01", focus.turn, f"turno {focus.turn}: formulario repetido (ya salió en el turno {forms[0].turn})"
            )
        return passed("ENV-01", f"formulario en el turno {focus.turn}", turn=focus.turn)
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


# ── ENV-02: ¿el mensaje del formulario le responde al cliente? ─────────────
# El formulario sale con un mensaje que arma el código (producto, variantes,
# cantidad y subtotal; sin Flow, la lista de campos). Basta solo cuando lo que
# escribió el cliente es algo que ese mensaje cubre: una cantidad, un sí, la
# elección de una variante (incidente 2026-10-06, turno 9: «2»). Una pregunta,
# un aplazamiento, una queja o un traspaso piden un texto del bot: con el
# mensaje en la traza, ENV-02 ya no podía fallar (revisión del PR #392).
_SHORT_ANSWER_MAX_WORDS = 8
_INGEST_NOTE_RE = re.compile(r"^\s*\[[^\]]*\]\s*\n")
_QUANTITY_RE = re.compile(
    r"(?:(?:quiero|me llevo|dame|serian|seria|son|solo|solamente|las|los|unas?|unos)\s+)*"
    r"(?:\d{1,3}|una?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    r"(?:\s+(?:unidades?|por favor|porfa|gracias))*"
)
#: Lo que pide un texto del bot aunque el mensaje sea corto: una pregunta
#: (con o sin signos), un «pero», una queja o un «no».
_NEEDS_A_REPLY_RE = re.compile(
    r"[?¿]"
    r"|^(?:y\s+)?(?:cuant[oa]s?|cuando|donde|como|que|cual(?:es)?|por que|para que|tienen|hay|puedo|"
    r"pueden|podrian|se puede|hacen|venden|manejan)\b"
    r"|\b(?:pero|aunque|sin embargo|ademas|tambien|otra cosa|queja|reclamo|roto|rota|danad[oa]|"
    r"no me llego|no ha llegado|no llego)\b"
    r"|^no\b"
)


def _fold(text: str) -> str:
    """Minúsculas, sin tildes ni signos (salvo ¿ y ?), espacios simples."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s?¿]", " ", stripped.casefold()).split())


def _customer_message(turn: Turn) -> str:
    """Lo que escribió el cliente en el turno (todos los mensajes de la
    ráfaga), sin las notas que antepone el ingest."""
    texts = [m.text for m in turn.inbound if m.text.strip()] if turn.inbound else [turn.inbound_text]
    out = []
    for text in texts:
        while m := _INGEST_NOTE_RE.match(text):
            text = text[m.end():]
        out.append(text.strip())
    return "\n".join(t for t in out if t)


def _variant_values(turn: Turn, ctx: CheckContext) -> set[str]:
    draft = turn.draft or {}
    rows = [draft, *(i for i in draft.get("items") or [] if isinstance(i, dict))]
    values = {str(row.get(k) or "") for row in rows for k in ("aroma", "color", "diseno")}
    values |= {*ctx.aromas, *ctx.colors}
    return {v for v in (_fold(x) for x in values) if v}


def _form_message_answers(turn: Turn, ctx: CheckContext) -> bool:
    """¿El mensaje que arma el código con el formulario le responde al cliente?
    Solo si escribió un mensaje corto que ese mensaje cubre: una cantidad
    («2»), un sí (la señal `affirmation` del ingest o la misma regla) o la
    elección de una variante. Una pregunta, un aplazamiento, una queja o un
    traspaso necesitan un texto del bot."""
    if turn.trigger != "customer" or turn.signal == "deferral":
        return False
    message = _customer_message(turn)
    folded = _fold(message)
    if not folded or _NEEDS_A_REPLY_RE.search(folded) or detect_deferral(message):
        return False
    if len(folded.split()) > _SHORT_ANSWER_MAX_WORDS:
        return False
    return (
        turn.signal == "affirmation"
        or bool(_QUANTITY_RE.fullmatch(folded))
        or detect_purchase_affirmation(message)
        or any(re.search(rf"(?<!\w){re.escape(v)}(?!\w)", folded) for v in _variant_values(turn, ctx))
    )


@code_check("ENV-02")
def check_form_with_reply(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    applicable = [t for t in _form_turns(traj) if judged(traj, t) and _answers_written_message(t)]
    if not applicable:
        return not_judged("ENV-02", traj, "ningún formulario respondió a un mensaje escrito del cliente")
    for turn in applicable:
        # Un texto del bot siempre responde. El mensaje del formulario (lo que
        # el cliente leyó con la tarjeta) responde solo una cantidad, un sí o
        # una variante elegida.
        if turn.sent_texts or (turn.card_read_texts and _form_message_answers(turn, ctx)):
            continue
        lost = lost_narration(turn)
        detail = f"; narración descartada {quote(lost[0])}" if lost else ""
        what = "solo el mensaje del formulario" if turn.card_read_texts else "formulario sin texto"
        return failed(
            "ENV-02", turn.turn, f"turno {turn.turn}: {what} ante {quote(turn.inbound_text)}{detail}"
        )
    return passed("ENV-02")


@code_check("ENV-03")
def check_form_data_recorded(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    arrivals = [t for t in judged_turns(traj) if t.inbound_text.lower().startswith(_FORM_DATA_MARKER)]
    if not arrivals:
        return not_judged("ENV-03", traj, "el cliente no envió el formulario")
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
        if not judged(traj, turn):
            continue
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


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _public_payment_key() -> str:
    """Llave Nequi/Bre-B del negocio: dato PÚBLICO que el guion permite escribir
    (única excepción de ENV-05). Misma fuente que el agente: env
    `PAYMENT_NEQUI_NUMBER` o el default del operador."""
    from src.plugins.chats.agent.sales.config.payments import get_nequi_number

    return _digits(get_nequi_number())


@code_check("ENV-05")
def check_no_bank_data_or_shipping_value(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    public_key = _public_payment_key()
    for turn, text in sent_texts(traj):
        any_text = True
        for m in _BANK_DATA_RE.finditer(text):
            if public_key and _digits(m.group(0)).endswith(public_key):
                continue  # la llave Nequi del negocio es pública (config/payments.py)
            return failed(
                "ENV-05", turn.turn, f"turno {turn.turn}: dato de pago en texto «{m.group(0)}» {quote(text)}"
            )
        if _SHIPPING_VALUE_RE.search(text):
            return failed("ENV-05", turn.turn, f"turno {turn.turn}: valor de envío definitivo {quote(text)}")
    if not any_text:
        return not_judged("ENV-05", traj, "el bot no envió texto")
    return passed("ENV-05")


@code_check("ENV-07")
def check_receiver_name_before_register(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    attempts = [(t, tc) for t in judged_turns(traj) for tc in t.tools_named("register_order")]
    if not attempts:
        return not_judged("ENV-07", traj, "no se intentó registrar la orden")
    if is_legacy(traj) or all(tc.ok is None for _, tc in attempts):
        return unknown("ENV-07", "sin resultado de register_order")
    for turn, tc in attempts:
        if tc.error == "missing_receiver_name":
            return failed("ENV-07", turn.turn, f"turno {turn.turn}: register_order rechazado por missing_receiver_name")
    return passed("ENV-07")
