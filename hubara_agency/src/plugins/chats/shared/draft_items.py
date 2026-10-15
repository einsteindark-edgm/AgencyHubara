"""Ítems del borrador del pedido — lector único, puro (sandbox-safe).

El borrador (`episodes[-1].order_draft`) guarda un ítem por producto en
`items` desde 2026-09-23. Los borradores anteriores tienen UN producto en los
slots planos: este lector los devuelve como un solo ítem, así ningún lector
tiene que saber de qué época es el borrador.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Campos que pertenecen a UN producto del pedido (el resto de los slots —
# envío, pago, notas — son del pedido completo).
ITEM_FIELDS: tuple[str, ...] = ("producto", "aroma", "color", "diseno", "cantidad")
#: Lo que cada producto del pedido necesita para salir de la etapa de variantes.
VARIANT_SLOTS: tuple[str, ...] = ("aroma", "color", "cantidad")
#: `order_draft[NOT_OFFERED_KEY]`: {clave del producto: atributos que el
#: catálogo no ofrece} (incidente del 2026-10-09: una vela sin colores nunca
#: salía de la etapa de variantes). Lo escribe `set_order_slot`.
NOT_OFFERED_KEY = "sin_opciones"
#: `order_draft[TO_CHOOSE_KEY]`: {clave del producto: {opción: valores}}, las
#: opciones reales de Medusa (dos valores o más) que el cliente escoge: la
#: «Color» de las velas de deseos, el «Signo» del Duo Zodiacal (2026-10-14,
#: ···0686: sin etiquetas «Color:», el color se marcaba «sin opciones» y el
#: pedido se registró sin él). Lo escribe `set_order_slot`; un borrador sin
#: ella se lee como antes.
TO_CHOOSE_KEY = "por_elegir"
#: Donde puede quedar anotada la elección de una opción de Medusa (el modelo
#: anota la «Color» como `color` o como `diseno`: pedido 78).
CHOICE_FIELDS: tuple[str, ...] = ("color", "diseno", "aroma")


def draft_items(draft: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Los productos del borrador, en orden, cada uno con sus variantes."""
    if not isinstance(draft, dict):
        return []
    items = draft.get("items")
    if isinstance(items, list):
        return [dict(i) for i in items if isinstance(i, dict) and i]
    slots = draft.get("slots")
    if not isinstance(slots, dict):
        return []
    legacy = {k: slots[k] for k in ITEM_FIELDS if slots.get(k)}
    return [legacy] if legacy else []


def product_key(name: Any) -> str:
    """Clave para reconocer el mismo producto escrito distinto
    ("Duo Zodiacal" / "dúo  zodiacal")."""
    folded = unicodedata.normalize("NFKD", str(name or "")).casefold()
    return " ".join(
        "".join(c for c in folded if not unicodedata.combining(c)).split()
    )


def find_product(products: list[Any], name: Any) -> Any | None:
    """Producto cuyo título o handle es ``name`` (sin acentos/mayúsculas)."""
    wanted = product_key(name)
    if not wanted:
        return None
    return next(
        (p for p in products if wanted in (product_key(p.title), product_key(p.handle))),
        None,
    )


def chooses(values: Any, options: list[str]) -> bool:
    """¿Alguno de `values` (lo anotado o lo que dice una línea) nombra uno de
    los valores de la opción? Por frase completa, sin tildes ni mayúsculas:
    «Multicolor», «Rojo y verde, Multicolor» o un `variant_label` como
    «Multicolor - Navidad» lo nombran; «Rojo» no nombra «Rojo y verde»."""
    raw = [values] if isinstance(values, str) else list(values or ())
    said = [f" {_words(v)} " for v in raw if isinstance(v, str) and v.strip()]
    wanted = [f" {_words(o)} " for o in options if _words(o)]
    return any(w in s for w in wanted for s in said)


def _words(text: str) -> str:
    """El texto sin tildes ni mayúsculas y con solo letras y números."""
    return " ".join(re.findall(r"\w+", product_key(text)))


def to_choose(draft: dict[str, Any] | None, producto: Any) -> dict[str, list[str]]:
    """Las opciones de Medusa que el producto pide escoger (`TO_CHOOSE_KEY`)."""
    marks = draft.get(TO_CHOOSE_KEY) if isinstance(draft, dict) else None
    found = marks.get(product_key(producto)) if isinstance(marks, dict) else None
    if not isinstance(found, dict):
        return {}
    return {str(k): [str(v) for v in vs] for k, vs in found.items() if isinstance(vs, list) and vs}


def missing_variants(draft: dict[str, Any] | None, item: dict[str, Any]) -> list[str]:
    """Lo que le falta a un ítem para salir de la etapa de variantes: aroma,
    color y cantidad, menos lo que el catálogo no le ofrece a su producto
    (`order_draft[NOT_OFFERED_KEY]`), y cada opción de Medusa que el producto
    pide escoger (`TO_CHOOSE_KEY`) sin un valor suyo en el color, el diseño o
    el aroma del ítem."""
    marks = draft.get(NOT_OFFERED_KEY) if isinstance(draft, dict) else None
    skip = marks.get(product_key(item.get("producto"))) if isinstance(marks, dict) else None
    not_offered = set(skip) if isinstance(skip, list) else set()
    options = to_choose(draft, item.get("producto"))
    chosen = [item.get(k) for k in CHOICE_FIELDS]
    out = [k for k in VARIANT_SLOTS if k not in options and not str(item.get(k) or "").strip() and k not in not_offered]
    return out + [axis for axis, values in options.items() if not chooses(chosen, values)]
