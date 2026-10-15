"""Cajas o velas: las dos cuentas de una cantidad de un producto que se cobra
por caja (puro; laboratorio de ···0686, 2026-10-14).

Las velas de deseos se cobran por caja (12 la cuadrada, 10 la ondulada). «Dos
cajas de 12» se anotó `cantidad: 24` y el resumen salió con 24 cajas. Desde
una caja entera, la cantidad puede ser de cajas o de velas: estas son las dos
lecturas, con lo que traen y lo que cuestan. Las lee Jev (capacidad `cajas`)
y, si no se sabe, el modelo (`set_order_slot`).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.shared.product_truth import numbers_in

#: Un pedido al por mayor es de más de 20 unidades (TOOLS.md, «Pide >20
#: unidades»); en lo que se cobra por caja, las unidades son cajas.
BULK_UNITS = 20


def needs_reading(cantidad: int, por_caja: int | None) -> bool:
    """¿La cantidad puede ser de velas? Desde una caja entera (24 de 12, no 2)."""
    return bool(por_caja and por_caja >= 2 and cantidad >= por_caja)


def as_boxes(cantidad: int, por_caja: int, precio: int | None) -> str:
    """«24 cajas = 288 velas, $600.000»."""
    price = f", {format_cop(cantidad * precio)}" if precio else ""
    return f"{cantidad} cajas = {cantidad * por_caja} velas{price}"


def as_candles(cantidad: int, por_caja: int, precio: int | None) -> str:
    """«24 velas = 2 cajas, $50.000», o que no son cajas enteras."""
    if cantidad % por_caja:
        return f"{cantidad} velas no son cajas enteras (cada caja trae {por_caja})"
    boxes = cantidad // por_caja
    price = f", {format_cop(boxes * precio)}" if precio else ""
    return f"{cantidad} velas = {boxes} {'caja' if boxes == 1 else 'cajas'}{price}"


def candle_counts(text: str | None, boxes: Sequence[int]) -> list[int]:
    """Las cifras del cliente que pasan de 20 pero, contadas en velas, caben
    en 20 cajas o menos (36 con cajas de 12: 3 cajas), en orden y sin repetir.
    Solo esas pueden ser un pedido al por mayor que no lo es (laboratorio
    2026-10-14: «me vendes 36 velitas?» se pasó a un colega). Lo decide Jev
    (`por_mayor`)."""
    top = BULK_UNITS * max(boxes, default=0)
    out: list[int] = []
    for raw in numbers_in(text or ""):
        if raw.isdigit() and BULK_UNITS < int(raw) <= top and int(raw) not in out:
            out.append(int(raw))
    return out


def other_box_named(text: str | None, own: int, sizes: Mapping[str, int]) -> str | None:
    """El otro producto por caja cuyo tamaño de caja nombró el cliente, si no
    nombró también el de este («Dos cajas de 12» y el asesor anota la
    ondulada, que trae 10: la cuadrada). None si no. Si pide ese, lo decide
    Jev (`caja_nombrada`)."""
    said = {int(raw) for raw in numbers_in(text or "") if raw.isdigit()}
    if own in said:
        return None
    return next((name for name, size in sizes.items() if size != own and size in said), None)


def loose_candles_note(boxes: Sequence[tuple[str, int]], cifras: Sequence[int]) -> str:
    """Lo que se cobra por caja y las cifras del cliente contadas en velas en
    cada caja: lo que lee el modelo cuando un «por mayor» eran velas sueltas
    (`escalate_to_human` y la retención de `send_reply`, capacidad `por_mayor`)."""
    sizes = ", ".join(f"{name} (caja de {units} velas)" for name, units in boxes)
    counts = "; ".join(f"en {name}, {as_candles(n, units, None)}" for n in cifras for name, units in boxes)
    return (
        f"Se cobran por caja: {sizes}. Si el cliente cuenta velas: {counts}; un pedido al por mayor es de más "
        f"de {BULK_UNITS} cajas."
    )


__all__ = [
    "BULK_UNITS", "as_boxes", "as_candles", "candle_counts", "loose_candles_note", "needs_reading", "other_box_named",
]
