"""Cajas o velas: las dos cuentas de una cantidad de un producto que se cobra
por caja (puro; laboratorio de ···0686, 2026-10-14).

Las velas de deseos se cobran por caja (12 la cuadrada, 10 la ondulada). «Dos
cajas de 12» se anotó `cantidad: 24` y el resumen salió con 24 cajas. Desde
una caja entera, la cantidad puede ser de cajas o de velas: estas son las dos
lecturas, con lo que traen y lo que cuestan. Las lee Jev (capacidad `cajas`)
y, si no se sabe, el modelo (`set_order_slot`).
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.pricing import format_cop


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


__all__ = ["as_boxes", "as_candles", "needs_reading"]
