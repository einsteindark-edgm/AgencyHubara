"""El producto del que habla un texto y lo que tiene: aromas, colores y
presentaciones (puro, sin I/O).

Incidente 2026-10-13 (bot V2): con 2 Velas de deseos cuadradas en el pedido,
el bot escribió «vienen en Chanel, Drakar y Ylang Ylang» (aromas de la
Plegaria de Luz). `send_reply` arma con esto la entrada de las capacidades
`aroma_ajeno`, `color_ajeno` y `variante_ajena` —qué productos están en foco
y qué tienen— y, si alguna encuentra algo ajeno, el mensaje con que el texto
vuelve al modelo. Qué es ajeno lo decide el paquete, no este módulo.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from src.plugins.chats.shared.draft_items import draft_items, find_product, product_key
from src.plugins.chats.shared.product_truth import mentioned_labels
from src.sdk.catalogkit import stem_word
from src.sdk.connectorkit import parse_variant_colors, parse_variant_tags

#: Palabras de un título que no distinguen el producto (todas las velas las llevan).
_GENERIC_WORDS = frozenset({"vela", "velas", "velita", "velitas", "velon", "velones"})
#: Una palabra del título nombra su producto desde 5 letras («luz», «cubo» no)…
_NAMING_FROM = 5
#: …y si no la comparten más de dos títulos («las de deseos» = las dos velas de deseos).
_NAMING_UP_TO = 2
#: Los valores de relleno de Medusa: un producto de una sola variante trae
#: «Unico» o «Default option value», que no son presentaciones para escoger.
_PLACEHOLDER_VALUES = frozenset({"unico", "default option value"})
#: La opción de Medusa que es el color del producto (las velas de deseos).
_COLOR_OPTIONS = frozenset({"color", "colores"})


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", product_key(text))


def _named_by_a_word(products: Sequence[Any], text: str) -> list[Any]:
    """Los productos que el texto nombra con una palabra de su título que casi
    ningún otro título tiene («la plegaria» = Plegaria de Luz, «los corazones»
    = Cubo de corazón: sin género ni número)."""
    by_word: dict[str, list[Any]] = {}
    for product in products:
        for word in dict.fromkeys(_words(getattr(product, "title", "") or "")):
            if len(word) >= _NAMING_FROM and word not in _GENERIC_WORDS:
                by_word.setdefault(stem_word(word), []).append(product)
    said = {stem_word(word) for word in _words(text)}
    return [p for word, named in by_word.items() if word in said and len(named) <= _NAMING_UP_TO for p in named]


def focus_products(products: Sequence[Any], *, draft: dict[str, Any] | None, text: str) -> list[Any]:
    """Los productos de los que habla el texto: los del pedido, los que nombra
    por su título y los que nombra con una palabra propia del título (en ese
    orden, sin repetir). Un producto del pedido que no está en el catálogo no
    entra: sin sus aromas no se juzga."""
    products = list(products)
    focus = [find_product(products, item.get("producto")) for item in draft_items(draft)]
    by_title = {product_key(p.title): p for p in products if getattr(p, "title", None)}
    focus += [by_title[product_key(t)] for t in mentioned_labels(text, [p.title for p in by_title.values()])]
    focus += _named_by_a_word(products, text)
    seen: set[str] = set()
    out: list[Any] = []
    for product in focus:
        if product is not None and product.handle not in seen:
            seen.add(product.handle)
            out.append(product)
    return out


def aromas_of(product: Any) -> list[str]:
    return list(parse_variant_tags(getattr(product, "tags", None)).aromas)


def _real_options(product: Any) -> dict[str, list[str]]:
    """Las opciones de Medusa con dos valores o más que no son relleno."""
    options = getattr(product, "options", None) or {}
    real = {
        str(name): [str(v) for v in values if product_key(v) not in _PLACEHOLDER_VALUES]
        for name, values in options.items()
    }
    return {name: values for name, values in real.items() if len(values) >= 2}


def colors_of(product: Any) -> list[str]:
    """Los colores del producto. Con el mapeo variante→color de la metadata
    (Duo Zodiacal), su paleta: las etiquetas pueden estar viejas. Si no, sus
    etiquetas «Color: …» y los valores de una opción «Color» (las velas de deseos)."""
    by_variant = parse_variant_colors(getattr(product, "metadata", None))
    if by_variant:
        return list(dict.fromkeys(aliases[0] for aliases in by_variant.values()))
    tags = list(parse_variant_tags(getattr(product, "tags", None)).colors)
    option = [v for name, values in _real_options(product).items() if product_key(name) in _COLOR_OPTIONS for v in values]
    return list(dict.fromkeys([*tags, *option]))


def presentations_of(product: Any) -> list[str]:
    """Lo que se escoge de un producto con variantes de verdad (presentación,
    signo, diseño o tamaño); vacío si tiene una sola."""
    return [v for values in _real_options(product).values() for v in values]


def catalog_labels(products: Sequence[Any]) -> tuple[list[str], list[str]]:
    """Los aromas y los colores de todo el catálogo (sin repetir, en orden):
    las listas donde la regla busca lo que el texto nombra."""
    aromas = list(dict.fromkeys(a for p in products for a in aromas_of(p)))
    colors = list(dict.fromkeys(
        c for p in products for c in [*parse_variant_tags(getattr(p, "tags", None)).colors, *colors_of(p)]
    ))
    return aromas, colors


def focus_lines(focus: Sequence[Any]) -> tuple[str, ...]:
    """Cada producto en foco con sus aromas (el mensaje con varios productos)."""
    return tuple(f"{p.title}: {', '.join(aromas_of(p)) or 'sin aromas en el catálogo'}" for p in focus)


def _listed(values: Sequence[str], empty: str) -> str:
    return ", ".join(values) if values else empty


def option_sheets(focus: Sequence[Any]) -> tuple[str, ...]:
    """Lo que Jev lee de cada producto con presentaciones para escoger (`variante_ajena`):
    sin ninguno, no se le pregunta. El formato es el que se midió con la sonda (2026-10-13)."""
    return tuple(
        f"Producto del pedido (del que hablan): {p.title}.\n"
        f"Aromas que tiene: {_listed(aromas_of(p), 'no tiene aromas para escoger')}.\n"
        f"Colores que tiene: {_listed(colors_of(p), 'no tiene colores para escoger')}.\n"
        f"Presentaciones que tiene: {', '.join(presentations_of(p))}."
        for p in focus if presentations_of(p)
    )


#: Lo que se le dice a Jev (y al modelo) de un producto de una sola presentación.
SINGLE_PRESENTATION = "una sola (no viene en otras formas, envases, tamaños ni versiones)"


def single_sheets(focus: Sequence[Any]) -> tuple[str, ...]:
    """La ficha de cada producto en foco de UNA sola presentación (la clase
    del 2026-09-25: «el Cubo Love también viene en vaso»). El paquete decide
    cuándo se la pregunta a Jev (solo si el texto nombra una presentación)."""
    return tuple(
        f"Producto del pedido (del que hablan): {p.title}.\n"
        f"Aromas que tiene: {_listed(aromas_of(p), 'no tiene aromas para escoger')}.\n"
        f"Colores que tiene: {_listed(colors_of(p), 'no tiene colores para escoger')}.\n"
        f"Presentaciones que tiene: {SINGLE_PRESENTATION}."
        for p in focus if not presentations_of(p)
    )


def _quoted(names: Iterable[str]) -> str:
    quoted = [f"«{n}»" for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " y " + quoted[-1]


def _what_it_has(focus: Sequence[Any], *, aromas: bool, colors: bool, variant: bool) -> str:
    """Lo que el producto sí tiene, solo de lo que el texto se equivocó."""
    if len(focus) > 1:
        parts = []
        if aromas:
            parts.append("Sus aromas: " + "; ".join(focus_lines(focus)) + ".")
        if colors:
            parts.append("Sus colores: " + "; ".join(f"{p.title}: {_listed(colors_of(p), 'sin colores')}" for p in focus) + ".")
        if variant:
            parts.append(
                "Sus presentaciones: " + "; ".join(f"{p.title}: {_listed(presentations_of(p), 'una sola')}" for p in focus) + "."
            )
        return " ".join(parts)
    product = focus[0]
    parts = []
    if aromas:
        own = aromas_of(product)
        parts.append(f"Su único aroma es {own[0]}." if len(own) == 1 else f"Sus aromas son: {', '.join(own)}.")
    if colors:
        own = colors_of(product)
        parts.append(f"Su único color es {own[0]}." if len(own) == 1 else f"Sus colores son: {', '.join(own)}.")
    if variant:
        options = presentations_of(product)
        parts.append(
            f"Sus presentaciones son: {', '.join(options)}." if options
            else "Viene en una sola presentación (no en otras formas, envases, tamaños ni versiones)."
        )
    return " ".join(parts)


def option_problem(
    focus: Sequence[Any], *, aromas: Sequence[str] = (), colors: Sequence[str] = (), variant: bool = False
) -> str:
    """Qué no tiene el producto y qué sí tiene (aromas, colores, presentaciones);
    "" si no hay nada. Una pieza del mensaje con que el texto vuelve al modelo."""
    if not (aromas or colors or variant):
        return ""
    titles = " o ".join(p.title for p in focus)
    named = [
        f"{_quoted(values)} como {noun if len(values) == 1 else plural}"
        for values, noun, plural in ((aromas, "aroma", "aromas"), (colors, "color", "colores")) if values
    ]
    if named:
        count = len(aromas) + len(colors)
        lead = f"Le nombraste {' y '.join(named)} de {titles}, y ese producto no {'lo' if count == 1 else 'los'} tiene."
        if variant:
            lead += " También le ofreciste una presentación, diseño o tamaño que no tiene."
    else:
        lead = f"Le ofreciste a {titles} una presentación, diseño o tamaño que no tiene."
    return f"{lead} {_what_it_has(focus, aromas=bool(aromas), colors=bool(colors), variant=variant)}"


def correction_tail(tool: str = "send_reply") -> str:
    """El cierre del mensaje: corregir, o el mismo texto si el aviso no aplica."""
    return (
        f"Corrige el texto con lo que es cierto y vuelve a llamar {tool}. Si le hablabas de otro producto "
        f"o le decías que no aplica, vuelve a llamar {tool} con el mismo texto."
    )


__all__ = [
    "aromas_of",
    "catalog_labels",
    "colors_of",
    "correction_tail",
    "focus_lines",
    "focus_products",
    "option_problem",
    "option_sheets",
    "presentations_of",
    "single_sheets",
]
