"""«Enviar productos» de la app: lo que el operador elige del catálogo.

Una categoría (los chips), un nombre (el buscador) o las dos. La lista que ve
la app (``GET /api/chats/catalog?category=&q=``) y lo que sale al tocar
«Enviar» (``present_products`` con ``{category, q}``) salen de :func:`pick`: lo
que se ve es lo que se manda (caso 2026-10-13: «Enviar productos» mandaba el
catálogo entero y el operador no podía mandar solo lo que el cliente pidió).

Las categorías son las filas del menú del bot (``category_entries``): las
mismas, en el mismo orden y con «Otros» para los productos sin categoría.

P-28: este módulo importa SOLO ``src.sdk`` + módulos de ``chats``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.tools.ui_intents import category_entries, category_members
from src.sdk.catalogkit import normalize_label

#: El chip de «sin filtro».
ALL_LABEL = "Todas"


class UnknownCategory(Exception):
    """La categoría elegida ya no está en el catálogo (la copia cambió
    después de que la app pintó los chips)."""


@dataclass(frozen=True)
class Picked:
    products: list[Any]
    #: El nombre de la categoría elegida; None = todas.
    category: str | None = None


def category_options(products: list[Any], categories: list[Any]) -> list[dict[str, Any]]:
    """Los chips: «Todas · N» primero y una por categoría con cuántos
    productos tiene (``value`` = el slug que vuelve en ``category``)."""
    entries = category_entries(products, categories)
    return [
        _option("", ALL_LABEL, len(products)),
        *(_option(slug, label, len(members)) for slug, label, members in entries),
    ]


def _option(value: str, name: str, count: int) -> dict[str, Any]:
    return {"value": value, "name": name, "label": f"{name} · {count}", "count": count}


def category_labels(products: list[Any], categories: list[Any]) -> dict[str, list[str]]:
    """``handle`` → los nombres de sus categorías, en el orden de los chips."""
    labels: dict[str, list[str]] = {}
    for _slug, label, members in category_entries(products, categories):
        for product in members:
            labels.setdefault(product.handle, []).append(label)
    return labels


def pick(products: list[Any], categories: list[Any], *, category: str = "", q: str = "") -> Picked:
    """Los productos de la categoría ``category`` (slug; vacío = todas) cuyo
    nombre tiene TODAS las palabras de ``q`` (sin tildes ni mayúsculas, en
    cualquier orden), en el orden del catálogo."""
    chosen: str | None = None
    universe = list(products)
    slug = category.strip()
    if slug:
        found = category_members(slug, universe, categories)
        if found is None:
            raise UnknownCategory(slug)
        chosen, universe = found
    words = normalize_label(q).split()
    if words:
        universe = [p for p in universe if _has_words(p, words)]
    return Picked(products=universe, category=chosen)


def filter_label(picked: Picked | None, q: str) -> str:
    """Lo elegido en palabras («Velas Religiosas · «vela»»; vacío sin filtro):
    lo dice el botón «Enviar» y lo que queda en «Enviando…» de la app."""
    parts = [picked.category] if picked is not None and picked.category else []
    if q.strip():
        parts.append(f"«{q.strip()}»")
    return " · ".join(parts)


def _has_words(product: Any, words: list[str]) -> bool:
    name = normalize_label(f"{product.title} {product.handle.replace('-', ' ')}")
    return all(word in name for word in words)
