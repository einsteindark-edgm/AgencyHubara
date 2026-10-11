"""Ítems del borrador del pedido — lector único, puro (sandbox-safe).

El borrador (`episodes[-1].order_draft`) guarda un ítem por producto en
`items` desde 2026-09-23. Los borradores anteriores tienen UN producto en los
slots planos: este lector los devuelve como un solo ítem, así ningún lector
tiene que saber de qué época es el borrador.
"""
from __future__ import annotations

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


def missing_variants(draft: dict[str, Any] | None, item: dict[str, Any]) -> list[str]:
    """Lo que le falta a un ítem para salir de la etapa de variantes: aroma,
    color y cantidad, menos lo que el catálogo no le ofrece a su producto
    (`order_draft[NOT_OFFERED_KEY]`)."""
    marks = draft.get(NOT_OFFERED_KEY) if isinstance(draft, dict) else None
    skip = marks.get(product_key(item.get("producto"))) if isinstance(marks, dict) else None
    not_offered = set(skip) if isinstance(skip, list) else set()
    return [k for k in VARIANT_SLOTS if not str(item.get(k) or "").strip() and k not in not_offered]
