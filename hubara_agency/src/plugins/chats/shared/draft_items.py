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
