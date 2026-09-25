"""Nota de turno: lo que el cliente pidió o mostró y NO existe en el catálogo.

Incidente 2026-09-23: el cliente mandó la foto de una vela de dragón y
preguntó «¿y en vaso también?»; nada de eso existe y Ventas respondió solo con
otra tarjeta de catálogo, sin aclararlo. El cliente siguió creyendo que había
velas en vaso y el remarketing terminó afirmándolo. El detector es el mismo de
la guarda del remarketing (`chats/shared/product_truth`); acá se le NOMBRA al
LLM lo que no existe — en el A/B del remarketing la regla sola no alcanzó,
nombrar el término sí. Qué hacer con la nota lo dice SOUL.md.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.shared.product_truth import unavailable_terms


def build_catalog_gap_note(customer_text: str, products: list[Any]) -> str | None:
    """(mensaje del cliente, productos del catálogo) → nota del turno o None."""
    terms = unavailable_terms(customer_text, products)
    if not terms:
        return None
    named = ", ".join(f"«{term}»" for term in terms)
    return (
        f"Lo que el cliente pidió o mostró y NO existe en el catálogo: {named}. "
        "Díselo claro en tu respuesta, nombrándolo (eso no lo manejamos), y "
        "ofrécele la alternativa real más cercana del catálogo; no le respondas "
        "con solo el catálogo como si existiera: si le muestras productos, dilo "
        "en el `intro_text`. Si alguna de esas palabras no es un producto, "
        "forma, envase ni diseño que pide (una ciudad, un medio de pago, un material "
        "o ingrediente), ignórala y responde con lo que ya sabes."
    )


__all__ = ["build_catalog_gap_note"]
