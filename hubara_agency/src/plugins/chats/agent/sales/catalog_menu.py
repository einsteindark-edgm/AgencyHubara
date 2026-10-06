"""El menú de categorías del catálogo (incidente 2026-10-06).

Cuando el catálogo completo no cabe en un mensaje (la lista de productos de
WhatsApp acepta hasta 30), `present_products` le manda al cliente sus
categorías en una lista: una fila por categoría, con el id
`categoria:<slug>`. Cuando el cliente toca una, la traducción del inbound
(`translate.py`) se lo dice al LLM con ese slug, y el LLM le pide a
`present_products(category=…)` los productos de esa categoría.

Determinístico de punta a punta: el id de la fila decide, nunca el texto del
cliente. Puro (stdlib): lo usan la tool, el flush, la traducción del inbound
y el laboratorio.
"""
from __future__ import annotations

from collections.abc import Sequence

#: Prefijo del id de una fila del menú de categorías (el handle de un
#: producto nunca lleva «:», así que una fila de producto no se confunde).
CATEGORY_ROW_PREFIX = "categoria:"

#: La «categoría» de los productos que no tienen ninguna: la fila «Otros».
#: Empieza con guion bajo porque ningún slug de categoría empieza así.
UNCATEGORIZED = "_sin_categoria"
UNCATEGORIZED_LABEL = "Otros"

#: Lo que el cliente lee debajo del texto del asesor en el menú.
CATEGORY_MENU_GUIDE = "Elige una categoría y te muestro sus productos."

_CHOICE_OPEN = "[el cliente eligió la categoría: "
_CHOICE_ID = ' (category="'
_CHOICE_CLOSE = '")]'


def category_row_id(slug: str) -> str:
    """El id de la fila del menú para la categoría `slug`."""
    return f"{CATEGORY_ROW_PREFIX}{slug}"


def category_from_row_id(row_id: object) -> str | None:
    """La categoría de una fila del menú, o None si la fila no es del menú
    (por ejemplo, la de un producto)."""
    if not isinstance(row_id, str) or not row_id.startswith(CATEGORY_ROW_PREFIX):
        return None
    return row_id[len(CATEGORY_ROW_PREFIX):].strip() or None


def category_choice_text(title: str, slug: str) -> str:
    """Lo que lee el LLM cuando el cliente elige una categoría del menú: el
    nombre que vio y el `category` que se le pasa a `present_products`."""
    return f"{_CHOICE_OPEN}{title.strip() or slug}{_CHOICE_ID}{slug}{_CHOICE_CLOSE}"


def parse_category_choice(text: str) -> tuple[str, str] | None:
    """`(nombre, slug)` de un texto armado por `category_choice_text`, o None."""
    if not (text.startswith(_CHOICE_OPEN) and text.endswith(_CHOICE_CLOSE)):
        return None
    title, found, slug = text[len(_CHOICE_OPEN):-len(_CHOICE_CLOSE)].rpartition(_CHOICE_ID)
    return (title, slug) if found and slug else None


def category_menu_body(intro: str, more: Sequence[str] = ()) -> str:
    """El texto del menú: el del asesor, la guía y, si hay más categorías de
    las que caben en la lista (10), las demás por su nombre: el cliente puede
    escribir cualquiera."""
    parts = [part for part in (intro.strip(), CATEGORY_MENU_GUIDE) if part]
    names = [name for name in more if name]
    if names:
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} y {names[-1]}"
        parts.append(f"También tenemos: {listed}. Escríbeme la que quieras ver.")
    return "\n\n".join(parts)
