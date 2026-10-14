"""Builtins de los mapeos al catálogo (categoría, familia de color, ítem del
pedido, producto nombrado, enumeración) — PAQUETES_DE_DECISION.md F3.

Las opciones de estas capacidades no se saben al escribir el paquete: las
arma un builtin de clase `options` desde la entrada (`{opción: (etiqueta,
valor)}`): Jev lee la etiqueta y contesta la opción; la tabla lee el valor
como `opt['<pregunta>'][<opción>]`. Una opción que no está en la lista no
pasa.

Se cargan solo cuando una capacidad del paquete los pide (las reglas viven
en `use_cases/` y en el catálogo).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.respuestas import option_keys
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.plugins.chats.agent.sales.use_cases.order_draft import item_for_values
from src.plugins.chats.agent.sales.variant_enumeration import (
    MIN_COMBINATIONS,
    enumeration_candidates,
    find_enumerated_variants,
)
from src.plugins.chats.agent.sales.price_quotes import find_unexplained_amounts
from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.agent.sales.use_cases.fact_rules import (
    CouponWords,
    DenialWords,
    PaymentWords,
    StatusWords,
    coupon_claims,
    customer_figures,
    denial_claims,
    offering_sentences,
    payment_claims,
    says_any,
    status_claims,
)
from src.plugins.chats.shared.product_truth import foreign_labels
from src.sdk.catalogkit import family_of_color, normalize_label, resolve_category, resolve_color_family, stem_word

Options = dict[str, tuple[str, Any]]


def resolution(res: Any) -> dict[str, Any]:
    """`ColorFamilyResolution | None` → JSON (lo que usa `set_order_slot`)."""
    if res is None:
        return {"estado": None, "color": None, "familias": [], "candidatas": [], "tono": False}
    return {
        "estado": res.status,
        "color": res.canonical,
        "familias": list(res.families),
        "candidatas": list(res.candidates),
        "tono": bool(res.shade_requested),
    }


def _accepting(inp: Any) -> dict[str, int]:
    return {f"item_{k + 1}": k for k, ok in enumerate(inp.aceptan) if ok is True}


# ── reglas ──


def category_of_query(inp: Any) -> dict[str, str | None]:
    """`resolve_category`: exacto → contenido → parecido de texto ≥ 0,80."""
    matched = resolve_category(inp.query, list(inp.categories)).matched
    return {"categoria": matched.slug if matched else None}


def color_family(inp: Any) -> dict[str, Any]:
    """La tabla de familias del tenant (`resolve_color_family`)."""
    return resolution(resolve_color_family(inp.color, list(inp.colores), inp.familias))


def order_item_for_values(inp: Any) -> dict[str, Any]:
    """El ítem en curso, salvo que su producto rechace los datos y otro los acepte."""
    k = item_for_values(inp.actual, list(inp.aceptan))
    return {"item": k, "producto": inp.items[k]}


def named_titles(inp: Any) -> tuple[str, ...]:
    """Los títulos que la charla nombra tal cual."""
    return tuple(inp.named)


def enumerated_variants(inp: Any) -> tuple:
    hit = find_enumerated_variants(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return () if hit is None else (hit[0], tuple(hit[1]))


def unexplained_prices_rule(inp: Any, *, politica: str) -> list[str]:
    """Los montos del texto que no explica el precio del catálogo de los
    productos de los que habla (con el recargo del link) ni la política en una
    oración que dice una frase de `politica` (del paquete). Sin precios, no se
    juzga."""
    if not inp.prices:
        return []
    found = find_unexplained_amounts(
        inp.text, catalog_prices=inp.prices, policy_amounts=inp.policy,
        surcharge_rates=getattr(inp, "surcharges", ()), policy_context=lambda s: says_any(s, politica),
    )
    return list(dict.fromkeys(format_cop(u.amount) for u in found))


def _payment(inp: Any, words: dict[str, str]) -> list[tuple[str, str]]:
    """(lo que dice, lo cierto) de `pago_ajeno`, con las frases del paquete."""
    return payment_claims(
        inp.text, subtotal=inp.subtotal, cod_min=inp.cod_min, surcharges=inp.surcharges, words=PaymentWords(**words)
    )


def _status(inp: Any, words: dict[str, str]) -> list[tuple[str, str]]:
    return status_claims(
        inp.text, order=inp.order, stage=inp.stage, pay_status=inp.pay_status, words=StatusWords(**words)
    )


def _coupon(inp: Any, words: dict[str, str]) -> list[tuple[str, str]]:
    return coupon_claims(
        inp.text, known=inp.known, valid=inp.valid, valid_lines=inp.valid_lines, valid_percents=inp.valid_percents,
        words=CouponWords(**words),
    )


def _denial(inp: Any, words: dict[str, str]) -> list[tuple[str, str]]:
    return denial_claims(inp.text, titles=inp.titles, own=inp.own, products=inp.products, words=DenialWords(**words))


def foreign_labels_rule(inp: Any, *, ofrece: str, no_ofrece: str) -> list[str]:
    """La regla sola: las etiquetas del catálogo (aromas o colores) que una
    oración que ofrece (`ofrece`, y no `no_ofrece`) nombra y no son del
    producto del que habla. Las frases son del paquete."""
    text = offering_sentences(inp.text, offers=ofrece, not_offers=no_ofrece)
    return list(foreign_labels(text, own=inp.own, labels=inp.labels, others=inp.others))


# ── ítems ──


def unexplained_price_items(inp: Any, *, politica: str) -> list[dict[str, Any]]:
    return [{"term": term} for term in unexplained_prices_rule(inp, politica=politica)]


def _claim_items(claims: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [{"term": term, "fact": fact} for term, fact in claims]


def customer_figure_items(inp: Any) -> list[dict[str, Any]]:
    """Las cifras que escribió el cliente y no son de su pedido («$45.000»),
    con lo que suma su pedido (`inp.order`); sin eso no se juzga ninguna."""
    if not inp.order:
        return []
    return [{"term": format_cop(amount), "fact": inp.order} for amount in customer_figures(inp.customer, inp.figures)]


def payment_claim_items(inp: Any, **words: str) -> list[dict[str, Any]]:
    return _claim_items(_payment(inp, words))


def status_claim_items(inp: Any, **words: str) -> list[dict[str, Any]]:
    return _claim_items(_status(inp, words))


def coupon_claim_items(inp: Any, **words: str) -> list[dict[str, Any]]:
    return _claim_items(_coupon(inp, words))


def denial_claim_items(inp: Any, **words: str) -> list[dict[str, Any]]:
    return _claim_items(_denial(inp, words))


def foreign_label_items(inp: Any) -> list[dict[str, Any]]:
    """Lo que Jev juzga: TODO lo ajeno que el texto nombra (sin las frases de
    la regla sola: Jev lee si se ofrece)."""
    return [{"term": term} for term in foreign_labels(inp.text, own=inp.own, labels=inp.labels, others=inp.others)]


# ── vistas ──


def enumeration_found(inp: Any) -> dict[str, Any]:
    """Los aromas y colores del catálogo que el texto enumera."""
    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return {"found_scents": found.scents, "found_colors": found.colors}


# ── estado ──


def category_request(inp: Any) -> str | None:
    query = (inp.query or "").strip()
    if not query or not inp.categories:
        return None
    labels = {c.slug: c.label for c in inp.categories}
    return f"El cliente pidió ver la categoría: «{query}»\nCategorías del catálogo: {', '.join(labels.values())}"


def color_request(inp: Any) -> str | None:
    if not normalize_label(inp.color or "") or not inp.colores:
        return None
    return "\n".join([
        f"Producto: {inp.producto}" if inp.producto else "Producto del pedido",
        f"Colores del producto: {', '.join(inp.colores)}",
        f"El cliente pidió el color: «{inp.color.strip()}»",
    ])


def order_item_data(inp: Any) -> str | None:
    """Solo cuando dos o más productos del pedido aceptan los datos."""
    candidates = _accepting(inp)
    if len(candidates) < 2:
        return None
    values = ", ".join(f"{field} «{value}»" for field, value in inp.valores.items())
    lines = ["PRODUCTOS DEL PEDIDO", *(f"- {inp.items[k]}" for k in candidates.values()),
             f"DATOS QUE LLEGAN SIN PRODUCTO: {values}"]
    window = customer_window(list(inp.events), burst_wamids=set(), burst_size=0)
    if window.lines:
        lines += ["CONVERSACIÓN — lo último", *window.lines]
    return "\n".join(lines)


def chat_and_catalog(inp: Any) -> str | None:
    if not inp.text.strip() or not inp.titles:
        return None
    return (
        "Conversación de una tienda con un cliente (lo último al final):\n"
        f"{inp.text.strip()}\n\nProductos del catálogo: " + ", ".join(inp.titles) + "."
    )


def text_and_product_labels(inp: Any, *, noun: str, items: list[dict[str, Any]] | None = None) -> str | None:
    """El texto y los productos de los que habla con lo que tienen (`noun`:
    aromas, colores), si la regla propone algo ajeno (sin candidatos no se le
    pregunta a Jev)."""
    if not items:
        return None
    # Sonda con Jev real (2026-10-13): con varios productos, una línea por
    # producto con sus aromas le rinde peor (0,11-0,16 a una oferta real) que
    # los títulos juntos y los aromas juntos (0,23-0,68; lo que no se ofrece ≤ 0,13).
    return (
        f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}\n\n"
        f"Producto del pedido (del que hablan): {' y '.join(inp.products)}. Sus {noun}: {', '.join(inp.own)}."
    )


def text_and_product_prices(inp: Any, *, items: list[dict[str, Any]] | None = None) -> str | None:
    """El texto y los productos de los que habla con su precio del catálogo,
    si la regla propone algún monto (sin candidatos no se le pregunta a Jev)."""
    if not items:
        return None
    return (
        f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}\n\n"
        f"Producto del pedido (del que hablan) y su precio en el catálogo: {'; '.join(inp.products)}."
    )


def text_and_claim_facts(inp: Any, *, items: list[dict[str, Any]] | None = None) -> str | None:
    """El texto y lo que dice el sistema de cada cosa que la regla propone
    (pago, estado, cupón, negación); sin candidatos no se le pregunta a Jev."""
    if not items:
        return None
    facts = list(dict.fromkeys(item["fact"] for item in items))
    return (
        f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}\n\n"
        f"Lo que dice el sistema: {'; '.join(facts)}."
    )


def customer_and_reply_facts(inp: Any, *, items: list[dict[str, Any]] | None = None) -> str | None:
    """Lo que escribió el cliente, lo que la tienda le va a responder y lo que
    dice el sistema de su pedido (`cifra_del_cliente`); sin candidatos no se
    le pregunta a Jev."""
    if not items:
        return None
    facts = list(dict.fromkeys(item["fact"] for item in items))
    return (
        f"Mensaje del cliente:\n{inp.customer.strip()}\n\n"
        f"Texto que la tienda le va a responder por WhatsApp:\n{inp.text.strip()}\n\n"
        f"Lo que dice el sistema: {'; '.join(facts)}."
    )


def text_and_option_sheets(inp: Any, *, words: str, ofrece: str) -> str | None:
    """El texto y la ficha de cada producto con presentaciones para escoger
    (`variante_ajena`) si el texto ofrece algo (una frase de `ofrece`) o
    nombra una presentación (`words`: «vaso, tamaño, caja…»); la de los de una
    sola presentación, solo con una de `words`. Las dos listas son de la
    tienda, en el paquete. Sin ninguna ficha, no se le pregunta a Jev
    (premortem del PR #426: «Quedo atenta» era una llamada por texto)."""
    triggers = {stem_word(w) for w in normalize_label(words.replace(",", " ")).split()}
    said = {stem_word(w) for w in normalize_label(inp.text).split()}
    names = bool(triggers & said)
    offers = names or says_any(inp.text, ofrece)
    sheets = [*(inp.sheets if offers else ()), *(inp.single if names else ())]
    if not sheets:
        return None
    return f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}\n\n" + "\n\n".join(sheets)


def text_with_lists(inp: Any) -> str | None:
    """El texto, solo si enumera una lista que decidir (aromas o colores)."""
    from src.plugins.chats.agent.sales.variant_enumeration import MIN_ENUMERATED

    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    if max(len(found.scents), len(found.colors)) < MIN_ENUMERATED:
        return None
    return f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}"


# ── opciones ──


def catalog_categories(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Una opción por categoría del catálogo: etiqueta = su nombre, valor = su slug."""
    labels = {c.slug: c.label for c in inp.categories}
    keys = option_keys([c.slug for c in inp.categories], reserved=reserved)
    return {key: (labels[slug], slug) for key, slug in keys.items()}


def product_colors(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Una opción por color del producto; el valor es la resolución completa
    (`tono`: otras palabras que las del catálogo, el bot confirma el tono)."""
    out: Options = {}
    for key, color in option_keys(inp.colores, reserved=reserved).items():
        family = family_of_color(color, inp.familias)
        out[key] = (color, {
            "estado": "resolved", "color": color, "familias": [family.label if family else color],
            "candidatas": [color], "tono": True,
        })
    return out


def accepting_items(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Los productos del pedido que aceptan los datos (`item_<n>`)."""
    return {key: (inp.items[k], {"item": k, "producto": inp.items[k]}) for key, k in _accepting(inp).items()}


def catalog_titles(inp: Any, *, reserved: Sequence[str]) -> Options:
    return {key: (title, title) for key, title in option_keys(inp.titles, reserved=reserved).items()}


# ── pisos ──


def no_enumeration_with_combinations(inp: Any, rule: Any, jev: Any) -> tuple:
    """Las combinaciones «Color · Aroma» de un cupón nunca van al selector."""
    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return () if found.combinations >= MIN_COMBINATIONS else jev
