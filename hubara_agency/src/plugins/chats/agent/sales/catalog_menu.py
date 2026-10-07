"""El menú de categorías del catálogo (incidente 2026-10-06).

Cuando el catálogo completo no cabe en un mensaje (la lista de productos de
WhatsApp acepta hasta 30), `present_products` le manda al cliente sus
categorías en una lista: una fila por categoría, con el id
`categoria:<slug>`. Cuando el cliente toca una, la traducción del inbound
(`translate.py`) se lo dice al LLM con ese id, y el LLM se lo pasa a
`present_products(category=…)`: con el id de una fila, la tool no le pregunta
al motor de decisiones (el id ya dice cuál es); con lo que escribió el
cliente, sí (capacidad `categoria`).

Determinístico de punta a punta: el id de la fila decide, nunca el texto del
cliente. Puro (stdlib): lo usan la tool, el flush, la traducción del inbound
y el laboratorio.
"""
from __future__ import annotations

from collections.abc import Sequence

#: Prefijo del id de una fila del menú de categorías (el handle de un
#: producto nunca lleva «:», así que una fila de producto no se confunde).
CATEGORY_ROW_PREFIX = "categoria:"

#: La «categoría» de los productos que no tienen ninguna (y de una categoría
#: «Otros» real, si también hay productos sin categoría): la fila «Otros».
#: Empieza con guion bajo porque ningún slug de categoría empieza así.
UNCATEGORIZED = "_sin_categoria"
UNCATEGORIZED_LABEL = "Otros"

#: Lo que el cliente lee debajo del texto del asesor en el menú.
CATEGORY_MENU_GUIDE = "Elige una categoría y te muestro sus productos."

_CHOICE_OPEN = "[el cliente eligió la categoría: "
_CHOICE_ID = ' (category="'
_CHOICE_CLOSE = '")]'
_SEPARATOR = "\n\n"


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
    nombre que vio y el `category` que le pasa tal cual a `present_products`
    (el id de la fila)."""
    return f"{_CHOICE_OPEN}{title.strip() or slug}{_CHOICE_ID}{category_row_id(slug)}{_CHOICE_CLOSE}"


def parse_category_choice(text: str) -> tuple[str, str] | None:
    """`(nombre, slug)` de un texto armado por `category_choice_text`, o None."""
    if not (text.startswith(_CHOICE_OPEN) and text.endswith(_CHOICE_CLOSE)):
        return None
    title, found, row_id = text[len(_CHOICE_OPEN):-len(_CHOICE_CLOSE)].rpartition(_CHOICE_ID)
    slug = category_from_row_id(row_id)
    return (title, slug) if found and slug else None


def category_menu_tail(more: Sequence[str] = (), *, max_len: int) -> str:
    """Lo que el código escribe en el menú (sin el texto del asesor): la guía
    y, si hay más categorías de las que caben en la lista (10), las demás por
    su nombre (el cliente puede escribir cualquiera). Es el `customer_text`
    del envelope: el texto del asesor ya lo leen de los argumentos de la tool,
    y el flush puede cambiarlo por el neutro."""
    names = [name for name in more if name]
    tail = CATEGORY_MENU_GUIDE
    if names:
        tail += _SEPARATOR + _more_line(names, max_len - len(tail) - len(_SEPARATOR))
    return tail


def category_menu_body(intro: str, more: Sequence[str] = (), *, max_len: int) -> str:
    """El texto del menú, de hasta `max_len` caracteres: el del asesor y la
    cola que arma el código (`category_menu_tail`). WhatsApp corta el cuerpo
    por el FINAL: el espacio de la cola se reserva y lo que se recorta, si
    hace falta, es el texto del asesor."""
    tail = category_menu_tail(more, max_len=max_len)
    intro = intro.strip()
    room = max_len - len(tail) - len(_SEPARATOR)
    if not intro or room <= 1:
        return tail[:max_len]
    if len(intro) > room:
        intro = intro[: room - 1].rstrip() + "…"
    return f"{intro}{_SEPARATOR}{tail}"


def _more_line(names: list[str], room: int) -> str:
    """«También tenemos: A, B y C. …», con tantos nombres como quepan en
    `room` (los demás se cuentan: «y 4 más»)."""
    for shown in range(len(names), 0, -1):
        rest = len(names) - shown
        listed = names[:shown]
        if rest:
            joined = f"{', '.join(listed)} y {rest} más"
        elif len(listed) == 1:
            joined = listed[0]
        else:
            joined = f"{', '.join(listed[:-1])} y {listed[-1]}"
        line = f"También tenemos: {joined}. Escríbeme la que quieras ver."
        if len(line) <= room:
            return line
    return f"Tenemos {len(names)} categorías más: escríbeme la que quieras ver."
