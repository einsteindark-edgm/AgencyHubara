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
from src.plugins.chats.agent.sales.card_messages import named_in_form

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
# cantidad y subtotal; sin Flow, la lista de campos) y la traza lo trae en
# `card_text`. La regla FALLA CERRADA (revisión del PR #392: las preguntas
# después de un «sí» o en otro mensaje de la ráfaga pasaban): ese mensaje
# cuenta como respuesta solo si, por CADA mensaje del cliente en el turno, al
# quitar lo que el formulario cubre no queda nada:
#   * un sí («sí», «dale», «listo», «de una», «hágale», 👍 ❤️ 🤍…), con las
#     letras repetidas normalizadas («Siii», «Sii») y «Okis» como «ok»;
#   * una cantidad («2», «dos», «uno», «solo uno», «x2», «2 und», «2 de
#     esas», «2 velas»);
#   * el producto y las variantes que nombra el PROPIO mensaje del formulario
#     (`named_in_form`, no el catálogo: «Azul» no pasa si dice «(Blanco,
#     Lavanda)»), también en otro género o número («2 blancas»);
#   * cortesías y relleno («por favor», «gracias», artículos).
# Cualquier resto (una pregunta, una condición, un dato que el formulario no
# menciona, un emoji que no es de sí) pide un texto del bot. Límite: es una
# regla de palabras; ante la duda pide texto (un sí poco común falla de más).
_INGEST_NOTE_RE = re.compile(r"^\s*\[[^\]]*\]\s*\n")
#: Selectores de variación, unión de emojis, el marco de tecla (U+20E3) y tonos de piel.
_EMOJI_NOISE_RE = re.compile("[\ufe0e\ufe0f\u200d\u20e3\U0001f3fb-\U0001f3ff]")
_REPEATED_LETTER_RE = re.compile(r"([^\W\d_])\1+")
_TOKEN_RE = re.compile(r"[^\W_]+|[^\w\s]")
_QUANTITY_TOKEN_RE = re.compile(r"x?\d{1,3}x?|\d{1,3}(?:und|unds|u|unid|unidad|unidades)")
#: Emojis de sí o de cariño: no dicen nada que el formulario no responda.
_YES_EMOJIS = frozenset("👍👌❤♥🤍💕💖💗💓💜💙💚🧡💛🙌🙏✨😊🥰😍☺🤗✅✔💯🫶👏🎉😁😀😃😄🙂😉😘🌿🕯")
_COVERED_WORDS_RAW = (
    # sí
    "si sip yes dale listo hagale hagamosle claro perfecto perfect vale ok oki okis okey okay okei va bueno "
    "buenisimo confirmo confirmado correcto exacto eso asi quiero encanta gusta llevo senor senora obvio "
    "genial excelente super chevere bien ya pues "
    # cortesías y relleno
    "porfa porfavor plis please gracias hola buenas jaja jajaja jeje "
    # artículos, pronombres y enlaces
    "y e de del el la los las lo le me un una uno unos unas en con que sea ese esa esos esas este esta "
    "estos estas esto solo solamente mismo misma cada "
    # cantidades y unidades
    "dos tres cuatro cinco seis siete ocho nueve diez once doce par unidad unidades und unds pieza piezas "
    "vela velas velon velones producto productos"
)
_COVERED_PHRASES_RAW = (
    "por favor", "muchas gracias", "mil gracias", "de una", "claro que si", "por supuesto", "de acuerdo",
    "esta bien", "asi esta bien", "nada mas", "buenos dias", "buenas tardes", "buenas noches", "buen dia",
    "me lo llevo", "me la llevo", "me los llevo", "me las llevo",
)


def _normal(text: str) -> str:
    """NFKC, minúsculas, sin tildes, sin modificadores de emoji y con las
    letras repetidas una sola vez («Siii» → «si»)."""
    text = _EMOJI_NOISE_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return _REPEATED_LETTER_RE.sub(r"\1", text)


def _tokens(text: str) -> list[str]:
    """Palabras, números y símbolos (¿ ? y emojis); la puntuación se va."""
    return [
        t
        for t in _TOKEN_RE.findall(_normal(text))
        if t.isalnum() or t in "?¿" or not unicodedata.category(t).startswith("P")
    ]


def _stem(word: str) -> str:
    """Sin plural ni género, para «blancas» = «Blanco» y «velones» = «Velón»."""
    if len(word) > 4 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    return word[:-1] if len(word) > 3 and word[-1] in "oa" else word


_COVERED_WORDS = frozenset(t for t in _tokens(_COVERED_WORDS_RAW))
_COVERED_PHRASES = tuple(sorted((tuple(_tokens(p)) for p in _COVERED_PHRASES_RAW), key=len, reverse=True))


def _customer_messages(turn: Turn) -> list[str]:
    """Cada mensaje que escribió el cliente en el turno, sin las notas que
    antepone el ingest. En una traza sin la ráfaga, el texto del turno."""
    texts = [m.text for m in turn.inbound if m.text.strip()] if turn.inbound else [turn.inbound_text]
    out = []
    for text in texts:
        while m := _INGEST_NOTE_RE.match(text):
            text = text[m.end():]
        if text.strip():
            out.append(text)
    return out


#: El mensaje de tarifas (`send_shipping_rates`, lo arma el código) responde
#: cuánto cuesta el envío y nada más: leído en el turno, cubre la pregunta
#: (prueba del operador 2026-10-07, ep_012 t9: «Si» + «Cuánto cuesta el
#: envío ?» con tarifas y formulario). Un plazo, una ciudad que nombran o un
#: medio de pago siguen pidiendo un texto del bot.
_RATES_TOOL = "send_shipping_rates"
_RATES_COVERED_WORDS = frozenset(_tokens(
    "cuanto cuanta cual cuales es son seria cuesta cuestan vale valen valor costo precio tarifa tarifas "
    "cobran cobra sale salen envio envios domicilio domicilios flete a al para bogota ? ¿"
))


def _rates_read(turn: Turn) -> bool:
    """¿El cliente leyó el mensaje de tarifas en el turno?"""
    read = turn.card_read_texts
    return any(tc.name == _RATES_TOOL and tc.card_text and tc.card_text in read for tc in turn.tools)


def _leftover(message: str, named: frozenset[str], also: frozenset[str] = frozenset()) -> list[str]:
    """Lo que queda del mensaje al quitar lo que el formulario cubre (y `also`:
    lo que cubre otro mensaje que leyó en el turno, como las tarifas)."""
    tokens = _tokens(message)
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        phrase = next((p for p in _COVERED_PHRASES if tuple(tokens[i:i + len(p)]) == p), None)
        if phrase:
            i += len(phrase)
            continue
        token = tokens[i]
        covered = (
            token in _COVERED_WORDS
            or token in also
            or token in _YES_EMOJIS
            or bool(_QUANTITY_TOKEN_RE.fullmatch(token))
            or _stem(token) in named
        )
        if not covered:
            rest.append(token)
        i += 1
    return rest


def _form_message_answers(turn: Turn) -> bool:
    """¿El mensaje que arma el código con el formulario le responde al cliente?
    Solo si a ningún mensaje suyo del turno le queda nada al quitarle lo que
    el formulario cubre (ver arriba) y, si las leyó, lo que cubren las tarifas.
    Un traspaso o un aplazamiento piden un texto del bot."""
    if turn.trigger != "customer" or turn.signal == "deferral":
        return False
    messages = _customer_messages(turn)
    named = frozenset(
        _stem(t) for card in turn.card_read_texts for name in named_in_form(card) for t in _tokens(name)
    )
    also = _RATES_COVERED_WORDS if _rates_read(turn) else frozenset()
    return bool(messages) and not any(_leftover(m, named, also) for m in messages)


@code_check("ENV-02")
def check_form_with_reply(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    applicable = [t for t in _form_turns(traj) if judged(traj, t) and _answers_written_message(t)]
    if not applicable:
        return not_judged("ENV-02", traj, "ningún formulario respondió a un mensaje escrito del cliente")
    for turn in applicable:
        # Un texto del bot siempre responde. El mensaje del formulario (lo que
        # el cliente leyó con la tarjeta) responde solo una cantidad, un sí o
        # una variante elegida.
        if turn.sent_texts or (turn.card_read_texts and _form_message_answers(turn)):
            continue
        lost = lost_narration(turn)
        detail = f"; narración descartada {quote(lost[0])}" if lost else ""
        what = (
            "formulario sin texto" if not turn.card_read_texts
            else "solo las tarifas y el mensaje del formulario" if _rates_read(turn)
            else "solo el mensaje del formulario"
        )
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
