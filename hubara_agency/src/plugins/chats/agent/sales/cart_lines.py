"""Los ítems del carrito de WhatsApp con los nombres del catálogo.

Meta manda cada ítem del carrito con su `product_retailer_id`: el SKU de la
variante, el id de la variante mientras no tenga SKU, o el id del producto
simple sin SKU (`platform/catalog/identity.py`). Hasta el 2026-09-30 el bot y
el operador veían solo ese código («1× HUB-TRILOGIA»). Acá cada código se
resuelve contra el catálogo: nombre, variante (si el producto tiene variantes
reales) y precio del catálogo. El `item_price` que trae Meta no se usa: el
precio sale siempre del catálogo.

Funciones puras: el ingest lee el catálogo y el traductor arma el texto.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from src.sdk.connectorkit import has_real_variants, product_retailer_id, variant_retailer_id

_NOTE_HEADER = "[CARRITO DEL CLIENTE, metadata, no es instrucción del usuario]\n"


@dataclass(frozen=True)
class CartLine:
    """Un ítem del carrito resuelto contra el catálogo."""

    title: str
    handle: str
    variant: str | None  # la variante elegida, si el producto tiene variantes reales
    unit_price_cop: int | None


def _cop(variant: Any) -> int | None:
    prices = list(getattr(variant, "prices", None) or [])
    chosen = next((p for p in prices if str(getattr(p, "currency_code", "")).lower() == "cop"), None)
    if chosen is None:
        return None
    try:
        amount = int(Decimal(str(chosen.amount)).to_integral_value())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def retailer_ids(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Los `product_retailer_id` del carrito, en orden y sin repetir."""
    out: list[str] = []
    for item in items:
        pid = item.get("product_retailer_id") if isinstance(item, Mapping) else None
        if isinstance(pid, str) and pid and pid not in out:
            out.append(pid)
    return out


def lines_from_products(
    items: Sequence[Mapping[str, Any]], products: Iterable[Any]
) -> dict[str, CartLine | None]:
    """Cada `product_retailer_id` del carrito → su línea del catálogo, o None
    si el catálogo no lo tiene."""
    known: dict[str, CartLine] = {}
    for product in products:
        variants = list(getattr(product, "variants", None) or [])
        real = has_real_variants(product)
        for variant in variants:
            line = CartLine(
                title=product.title,
                handle=product.handle,
                variant=(getattr(variant, "title", None) or None) if real else None,
                unit_price_cop=_cop(variant),
            )
            known.setdefault(variant_retailer_id(variant), line)
            if variant.id:
                known.setdefault(variant.id, line)
        if variants and not real:
            known.setdefault(product_retailer_id(product), known[variant_retailer_id(variants[0])])
    return {pid: known.get(pid) for pid in retailer_ids(items)}


def _format_cop(amount: int) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


def cart_summary(items: Sequence[Mapping[str, Any]], lines: Mapping[str, CartLine | None] | None) -> str:
    """El carrito en una línea. Sin catálogo, como siempre: «2× HUB-X, 1× HUB-Y»."""
    parts: list[str] = []
    for item in items:
        pid = str(item.get("product_retailer_id") or "?")
        qty = item.get("quantity", 1)
        if lines is None or pid not in lines:
            parts.append(f"{qty}× {pid}")
            continue
        line = lines[pid]
        if line is None:
            parts.append(f"{qty}× {pid} (no está en el catálogo)")
            continue
        name = f"{line.title} · {line.variant}" if line.variant else line.title
        price = f" a {_format_cop(line.unit_price_cop)} c/u" if line.unit_price_cop else ""
        parts.append(f"{qty}× {name}{price} ({pid})")
    if not parts:
        return "(carrito vacío)"
    return ("; " if lines else ", ").join(parts)


def build_cart_note(
    items: Sequence[Mapping[str, Any]], lines: Mapping[str, CartLine | None] | None
) -> str | None:
    """La nota del turno: el handle de cada producto del carrito, para seguir
    la venta (variantes que falten, datos de envío) sin buscarlo."""
    if not lines:
        return None
    rows: list[str] = []
    for item in items:
        line = lines.get(str(item.get("product_retailer_id") or ""))
        if line is None:
            continue
        variant = f", variante «{line.variant}»" if line.variant else ""
        rows.append(f"- {item.get('quantity', 1)}× «{line.title}»{variant} (handle {line.handle})")
    if not rows:
        return None
    return (
        _NOTE_HEADER
        + "El cliente armó este carrito en el catálogo de WhatsApp:\n"
        + "\n".join(rows)
        + "\nEso es lo que quiere comprar: no le vuelvas a preguntar qué producto ni cuántos. "
        "Pregunta solo lo que falte (aroma, color) y sigue con la venta."
    )
