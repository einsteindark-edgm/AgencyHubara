"""La opción de Medusa escogida antes de cerrar (2026-10-14, ···0686).

Las Velas de deseos tienen la opción «Color» (Rojo y verde, Multicolor). El bot
armó el pedido sin que la clienta escogiera y `register_order` mandó el aroma
como `variant_label`: Medusa cayó a la primera variante y un colega tuvo que
preguntarle el color. Es un hecho del catálogo, no una decisión: un producto
con una opción de dos valores o más (`product_options.options_to_choose`) no
se confirma ni se registra sin uno de ellos, en la línea (color, diseño, aroma
o `variant_label`) o en el borrador (`draft_items.CHOICE_FIELDS`). Lo usan
`present_order_confirmation` y `register_order` (este, solo para el bot).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.use_cases.order_draft import get_active_draft
from src.plugins.chats.agent.sales.use_cases.product_options import options_to_choose
from src.plugins.chats.shared.draft_items import CHOICE_FIELDS, chooses, draft_items, product_key

#: Lo que la línea de una tool del cierre puede traer con la elección.
LINE_FIELDS: tuple[str, ...] = ("color", "diseno", "aroma", "variant_label")


async def unchosen_options(catalog: Any, items: Sequence[dict[str, Any]], metadata: dict[str, Any]) -> list[str]:
    """Lo que falta escoger, en palabras («el color de Velas de deseos
    cuadradas (Rojo y verde o Multicolor)»). Vacío si está todo, sin catálogo
    o si un producto no se encuentra (no saber no frena el cierre)."""
    if catalog is None:
        return []
    lines = draft_items(get_active_draft(metadata or {}))
    out: list[str] = []
    for item in items:
        try:
            product = await catalog.get_by_handle(str(item.get("handle")))
        except Exception:  # noqa: BLE001 — sin el producto no hay contra qué mirar
            continue
        options = options_to_choose(product)
        if not options:
            continue
        names = {product_key(product.title), product_key(product.handle)}
        said = [item.get(k) for k in LINE_FIELDS]
        drafted = [line.get(k) for line in lines if product_key(line.get("producto")) in names for k in CHOICE_FIELDS]
        out += [
            f"el {axis} de {product.title} ({' o '.join(values)})"
            for axis, values in options.items()
            if not (chooses(said, values) or chooses(drafted, values))
        ]
    return list(dict.fromkeys(out))


__all__ = ["LINE_FIELDS", "unchosen_options"]
