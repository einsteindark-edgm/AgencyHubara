"""Checks de código de la familia `descubrimiento` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    PRICE_RE,
    failed,
    fold,
    in_focus,
    is_legacy,
    judged,
    judged_turns,
    not_applicable,
    not_judged,
    our_quoted_message,
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

_DISCOVERY_STAGES = (None, "descubrimiento")
_MAX_QUESTIONS_BEFORE_SHOW = 2
_ORDINALS = {1: "primera", 2: "segunda", 3: "tercera"}

# Pedido de catálogo escrito por el cliente (o botón "Ver catálogo").
_CATALOG_BUTTON = "[el cliente tocó el botón: ver catálogo"
_CATALOG_REQUEST_RE = re.compile(
    r"\b(cat[aá]logo|mu[eé]strame|mu[eé]strales|qu[eé] (tienen|hay|manejan|venden)"
    r"|ver (las )?(velas|opciones|productos))\b",
    re.IGNORECASE,
)
# Framing del handoff: "Usuario respondió: <texto>. Siguiente paso: <instrucción>".
_HANDOFF_USER_RE = re.compile(r"usuario respondi[oó]:\s*(.*?)(?:siguiente paso:.*)?$", re.IGNORECASE | re.DOTALL)
_SELECTION_MARKER = "[el cliente seleccionó:"
#: La foto entra como texto con su descripción y, si se reconoció, el producto
#: nuestro que es (identificación de fotos, 2026-09-30): eso no lo escribió el
#: cliente.
_PHOTO_ANNOTATION_RE = re.compile(r"\[el cliente envió una foto: [^\]]*\]")
#: Decir que lo quiere (DES-08). «no quiero» no cuenta; «quiero ver/saber…» es
#: pedir información; «me gusta» y «me encanta» tampoco: que le guste no es que
#: lo quiera (operador, 2026-10-07), el bot le pregunta si se lo lleva.
_WANTS_RE = re.compile(
    r"(?<!no )\b(?:quiero(?! (?:ver|saber|preguntar|conocer|mirar|info))|quisiera|quer[ií]a"
    r"|me (?:lo |la |los |las )?llevo|me gustar?[ií]a|me quedo con"
    r"|voy con|vamos con|dame|deme|reg[aá]l[ae]me|pido|prefiero|escojo|elijo"
    r"|sep[aá]ra(?:me|r|s|mela|melo)?|ap[aá]rta(?:me|mela|melo)?|res[eé]rva(?:me|mela|melo)?"
    r"|compro|comprar|encargo|encargar)\b"
)
_NON_WORD_RE = re.compile(r"[^\w]+")
#: Contestar solo con el nombre del producto («el cubo love porfa») también es
#: elegirlo: lo que acompaña al nombre son estas palabras de relleno. Salvo que
#: conteste «¿cuál te gustó?» (ver `_asks_which_they_like`).
_FILLER_WORDS = frozenset({
    "el", "la", "los", "las", "un", "una", "ese", "esa", "este", "esta", "de", "del", "y", "entonces",
    "mejor", "si", "sí", "ok", "listo", "porfa", "por", "favor", "gracias", "pls",
})

_GROUNDING_TOOLS = frozenset({
    "search_products", "get_product_by_handle", "list_categories", "present_products",
    "present_product_detail", "present_product_gallery", "verify_order_for_checkout",
    "check_order_status", "register_order",
})
_MIN_TITLE_LEN = 4

# Una pregunta de opciones ("¿La sala o el dormitorio?") detrás de otra pregunta
# es parte de la misma: la concreta, no abre un tema nuevo.
_QUESTION_RE = re.compile(r"[^?]*\?")
_INTERROGATIVE_RE = re.compile(
    r"\b(qu[é]|cu[á]l(es)?|c[ó]mo|cu[á]nt[oa]s?|d[ó]nde|cu[á]ndo|qui[é]n(es)?)\b"
    r"|^(que|cual|cuales|como|cuanto|cuanta|donde|cuando|quien)\b",
    re.IGNORECASE,
)
_DISJUNCTION_RE = re.compile(r"\bo\b", re.IGNORECASE)
_AROMA_RE = re.compile(r"\baroma\w*", re.IGNORECASE)


def _show_index(traj: Trajectory) -> int | None:
    """Posición del primer turno que mostró catálogo, foto, galería o picker."""
    return next(
        (i for i, t in enumerate(traj.turns) if set(t.intents) & CATALOG_DISPLAY_INTENTS), None
    )


def _discovery_turns_before_show(traj: Trajectory) -> list[Turn]:
    show = _show_index(traj)
    scope = traj.turns if show is None else traj.turns[:show]
    return [t for t in scope if t.stage_in in _DISCOVERY_STAGES]


def _questions(text: str) -> list[str]:
    """Preguntas distintas de una burbuja (las de opciones se funden con la anterior)."""
    found: list[str] = []
    for raw in _QUESTION_RE.findall(text):
        body = re.split(r"[.!¡\n]", raw.split("¿")[-1] if "¿" in raw else raw)[-1].strip()
        is_options = bool(found) and not _INTERROGATIVE_RE.search(body) and _DISJUNCTION_RE.search(body)
        if not is_options:
            found.append(body)
    return found


def _customer_words(turn: Turn) -> str | None:
    """Lo que escribió el cliente en el turno, o None si no es un mensaje suyo."""
    if turn.trigger == "customer":
        return turn.inbound_text
    if turn.trigger == "handoff":
        m = _HANDOFF_USER_RE.search(turn.inbound_text)
        return m.group(1) if m else turn.inbound_text
    return None


@code_check("DES-01")
def check_max_questions_before_show(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    shown = _show_index(traj) is not None
    count = 0
    scope = _discovery_turns_before_show(traj)
    for turn in scope:
        for text in turn.sent_texts:
            if "?" not in text:
                continue
            count += 1  # el prefijo cuenta: las preguntas ya hechas siguen hechas
            if count > _MAX_QUESTIONS_BEFORE_SHOW and judged(traj, turn):
                where = "antes de mostrar productos" if shown else "sin llegar a mostrar productos"
                return failed(
                    "DES-01", turn.turn,
                    f"turno {turn.turn}: {_ORDINALS.get(count, f'{count}ª')} pregunta {where} {quote(text)}",
                )
    if in_focus(traj) and not any(judged(traj, t) for t in scope):
        return not_judged("DES-01", traj, "el turno no es de descubrimiento antes de mostrar productos")
    if not shown and count == 0:
        return not_applicable("DES-01", "sin descubrimiento")
    return passed("DES-01", f"{count} pregunta(s) antes de mostrar productos")


@code_check("DES-02")
def check_one_question_per_bubble(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    any_text = False
    for turn, text in sent_texts(traj):
        any_text = True
        questions = _questions(text)
        if len(questions) >= 2:
            return failed(
                "DES-02", turn.turn, f"turno {turn.turn}: {len(questions)} preguntas en una burbuja {quote(text)}"
            )
    if not any_text:
        return not_judged("DES-02", traj, "el bot no envió texto")
    return passed("DES-02")


def _is_catalog_request(turn: Turn) -> bool:
    words = _customer_words(turn)
    if words is None:
        return False
    low = words.lower()
    return _CATALOG_BUTTON in low or bool(_CATALOG_REQUEST_RE.search(low))


@code_check("DES-03")
def check_catalog_requested_sent(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    requests = [t for t in judged_turns(traj) if _is_catalog_request(t)]
    if not requests:
        return not_judged("DES-03", traj, "el cliente no pidió el catálogo")
    for turn in requests:
        if not set(turn.intents) & CATALOG_DISPLAY_INTENTS:
            return failed(
                "DES-03", turn.turn,
                f"turno {turn.turn}: pidió catálogo {quote(turn.inbound_text)} y no viajó catálogo, foto ni picker",
            )
    return passed("DES-03", f"{len(requests)} pedido(s) de catálogo atendidos")


def _titles(ctx: CheckContext) -> list[str]:
    if not ctx.catalog_available:
        return []
    return [t for t in ctx.product_titles if len(t) >= _MIN_TITLE_LEN]


# Montos que no salen del catálogo: umbrales y recargos de las formas de pago y
# valores de envío ("compras superiores a $45.000", "recargo de $3.000", "el
# envío sale en $12.000"). Se miran las palabras justo antes del monto.
_POLICY_AMOUNT_RE = re.compile(
    # «compras/pedidos desde», «a partir de»: el mínimo del contra entrega
    # (incidente del 2026-10-09, ···9824: «aplica para compras desde $45.000»).
    r"(superior(es)?|mayor(es)?|m[aá]s de|m[ií]nim[oa]|recargo|env[ií]o|domicilio|flete|contra ?entrega"
    r"|(compras|pedidos) (desde|de)|a partir de)"
    r"[^.$\n]{0,25}$",
    re.IGNORECASE,
)


def _catalog_price(text: str) -> str | None:
    for m in PRICE_RE.finditer(text):
        if not _POLICY_AMOUNT_RE.search(text[max(0, m.start() - 60) : m.start()]):
            return m.group(0)
    return None


def _naming(text: str, titles: list[str], known: frozenset[str] = frozenset()) -> str | None:
    """El precio o el producto que el texto nombra, salvo los productos que
    ya nombró un mensaje nuestro (`known`, en minúsculas y sin tildes)."""
    if price := _catalog_price(text):
        return price
    low = fold(text)
    return next((t for t in titles if fold(t) in low and fold(t) not in known), None)


def _named_by_us(turn: Turn, titles: list[str]) -> set[str]:
    """Los productos que nombró el mensaje nuestro que el cliente contesta
    (incidente del 2026-10-09, ···9824: la asesora mandó la trilogía con su
    enlace). El bot no los saca de la memoria."""
    ours = our_quoted_message(turn.inbound_text)
    return {fold(t) for t in titles if ours and fold(t) in fold(ours)}


@code_check("DES-05")
def check_search_before_naming(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    titles = _titles(ctx)
    grounded = False
    named = False
    known: set[str] = set()
    for turn in traj.turns:
        grounded = grounded or any(tc.name in _GROUNDING_TOOLS for tc in turn.tools)
        known |= _named_by_us(turn, titles)
        if not judged(traj, turn):
            continue  # el prefijo solo aporta búsquedas hechas y lo que nombramos
        for text in turn.sent_texts:
            name = _naming(text, titles, frozenset(known))
            if name is None:
                continue
            named = True
            if not grounded:
                return failed(
                    "DES-05", turn.turn,
                    f"turno {turn.turn}: nombró «{name}» sin buscar antes en el catálogo {quote(text)}",
                )
    if not named:
        return not_judged("DES-05", traj, "el bot no nombró productos ni precios")
    return passed("DES-05")


def _last_display_offers_products(previous: tuple[Turn, ...]) -> bool:
    last = next((t for t in reversed(previous) if set(t.intents) & CATALOG_DISPLAY_INTENTS), None)
    return last is not None and bool({"products_list", "product_detail"} & set(last.intents))


#: Una pregunta que pide decidir la compra: «¿te la llevas?», «¿cuál quieres?»,
#: «¿con cuál te quedas?», «¿prefieres el set o solo la calabaza?».
_DECISION_ASK_RE = re.compile(
    r"\b(?:llevas|llevar|llevamos|cu[aá]l(?:es)? quieres|te quedas|te animas|prefieres|eliges|elegir|escoges|escoger"
    r"|agrego|agregamos|anoto|anotamos|separo|separamos|aparto|apartamos|reservo|reservamos|compras|comprar)\b",
    re.IGNORECASE,
)
#: Una pregunta por lo que le gustó o le interesa: «¿cuál te gustó?», «¿alguna
#: te llamó la atención?», «dime cuál y te cuento los detalles».
_LIKING_ASK_RE = re.compile(
    r"\b(?:te gust[oó]|te gustan?|te llam[oó] la atenci[oó]n|te interesa\w*|te cuento|detalles?"
    r"|quieres (?:ver|saber|conocer|que te))\b",
    re.IGNORECASE,
)


def _last_ask(previous: tuple[Turn, ...]) -> str:
    """La última burbuja con pregunta que leyó el cliente antes de contestar
    ("" si el último turno en que el bot le escribió no preguntó nada)."""
    for turn in reversed(previous):
        if turn.read_texts:
            return next((text for text in reversed(turn.read_texts) if "?" in text), "")
    return ""


def _asks_which_they_like(ask: str) -> bool:
    """Prueba del operador 2026-10-07 (ep_012 t4): a «¿Cuál de las cuatro te
    gustó? Dime el nombre y te cuento los detalles» el cliente contestó «La de
    la calabaza». Dijo cuál le gustó, no que la quiera."""
    return not _DECISION_ASK_RE.search(ask) and bool(_LIKING_ASK_RE.search(ask))


def _wants(words: str, title: str, ask: str = "") -> bool:
    """¿El cliente dice que QUIERE el producto que nombra? Nombrarlo no es
    elegirlo: «se llama Luz Serena, la saqué de su catálogo» dice cómo se
    llama (laboratorio caso-fotos-0930-r7, 4567 t17), «¿tienen el Cubo Love?»
    pregunta si hay y «me gusta el Cubo Love» dice que le gusta. Sí lo es
    decir que lo quiere («la quiero», «me llevo», «sepárame»…) o contestar
    solo con su nombre («el cubo love porfa»), salvo que conteste cuál le
    gustó (`ask`: lo último que le preguntó el bot)."""
    low = words.lower()
    if _WANTS_RE.search(low):
        return True
    rest = _NON_WORD_RE.sub(" ", low.replace(title.lower(), " ")).split()
    name_only = "?" not in low and all(word in _FILLER_WORDS for word in rest)
    return name_only and not _asks_which_they_like(ask)


def _is_product_choice(traj: Trajectory, index: int, titles: list[str]) -> bool:
    turn = traj.turns[index]
    previous = traj.turns[:index]
    low = turn.inbound_text.lower()
    if low.startswith(_SELECTION_MARKER):
        return _last_display_offers_products(previous)
    words = _customer_words(turn)
    if words is None or low.startswith("["):
        return False
    words = _PHOTO_ANNOTATION_RE.sub(" ", words)
    shown = any(set(t.intents) & CATALOG_DISPLAY_INTENTS for t in previous)
    ask = _last_ask(previous)
    return shown and any(title.lower() in words.lower() and _wants(words, title, ask) for title in titles)


def _product_recorded(turn: Turn) -> bool:
    by_tool = any(tc.name == "set_order_slot" and tc.args.get("producto") for tc in turn.tools)
    return by_tool or bool((turn.draft or {}).get("producto"))


@code_check("DES-08")
def check_chosen_product_recorded(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    titles = _titles(ctx)
    choices = [
        t for i, t in enumerate(traj.turns) if judged(traj, t) and _is_product_choice(traj, i, titles)
    ]
    if not choices:
        return not_judged("DES-08", traj, "el cliente no eligió un producto de un catálogo mostrado")
    for turn in choices:
        if is_legacy(traj):
            if not turn.tool_attempted("set_order_slot"):
                return unknown("DES-08", f"turno {turn.turn}: legacy sin argumentos de tools")
            continue
        if not _product_recorded(turn):
            return failed(
                "DES-08", turn.turn,
                f"turno {turn.turn}: eligió {quote(turn.inbound_text)} y el producto no quedó anotado en el pedido",
            )
    return passed("DES-08", f"{len(choices)} elección(es) registradas")


@code_check("DES-09")
def check_design_before_aroma(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    scope = [t for t in _discovery_turns_before_show(traj) if judged(traj, t)]
    if not scope:
        return not_judged("DES-09", traj, "sin turnos de descubrimiento antes del catálogo")
    for turn in scope:
        for text in turn.sent_texts:
            if "?" in text and _AROMA_RE.search(text):
                return failed(
                    "DES-09", turn.turn, f"turno {turn.turn}: pregunta por aroma antes de mostrar productos {quote(text)}"
                )
    return passed("DES-09")


# ── DES-10: los precios que escribe el bot son los del catálogo ──────────────
# Run ebbc203d (2026-09-16): "tiene un valor de *$45.000 COP*" con el set a
# $49.500 (el 45.000 venía del anuncio). Misma mecánica que
# `verify_order_for_checkout` (price_quotes.find_unexplained_amounts).
from src.plugins.chats.agent.sales.card_texts import card_texts  # noqa: E402
from src.plugins.chats.agent.sales.config.shipping import (  # noqa: E402
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
)
from src.plugins.chats.agent.sales.price_quotes import (  # noqa: E402
    extract_cop_amounts,
    find_unexplained_amounts,
)
from src.plugins.chats.agent.sales.pricing import format_cop  # noqa: E402

_POLICY_AMOUNTS = (CASH_ON_DELIVERY_MIN_PRODUCTS_COP, SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP)


def _bot_texts(traj: Trajectory) -> list[tuple[Turn, str]]:
    """Todo lo que el bot le escribió al cliente: texto final + el texto de
    las tarjetas que redacta el LLM (`card_texts`: lista, botones, ficha…)."""
    out: list[tuple[Turn, str]] = []
    for t in traj.turns:
        if not judged(traj, t):
            continue
        out.extend((t, x) for x in t.sent_texts)
        out.extend((t, x) for tc in t.tools for x in card_texts(tc.name, tc.args))
    return out


@code_check("DES-10")
def check_prices_are_catalog_prices(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    with_amounts = [(t, x) for t, x in _bot_texts(traj) if extract_cop_amounts(x)]
    if not with_amounts:
        return not_judged("DES-10", traj, "el bot no escribió montos")
    if not ctx.catalog_available or not ctx.catalog_prices:
        return unknown("DES-10", "sin precios del catálogo")
    for turn, text in with_amounts:
        hits = find_unexplained_amounts(
            text, catalog_prices=ctx.catalog_prices, policy_amounts=_POLICY_AMOUNTS
        )
        if hits:
            hit = hits[0]
            return failed(
                "DES-10", turn.turn,
                f"turno {turn.turn}: monto {format_cop(hit.amount)} sin respaldo en el catálogo {quote(hit.sentence)}",
            )
    return passed("DES-10")
