"""Precio server-side de los ítems de un pedido desde el snapshot del catálogo.

D1.2b (2026-09-07): las tools de Meta Business Agent NO traen precios
(`register_order` manda `items[{handle, variant_label, quantity}]`). Antes de
llamar `RegisterOrderTool` (SEC-07: subtotal = Σ unit_price×qty), `chats`
resuelve cada ítem contra el snapshot: la variante por su etiqueta
("Lavanda, Blanco" / "Leo" / el título de la variante) y el precio en COP.

Puro: recibe los productos ya cargados (`{handle: CatalogProductDTO}`), no
toca red ni disco. La carga la hace el endpoint.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["PricedOrder", "price_order_items"]

_TOKEN_RE = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class PricedOrder:
    """Ítems con precio (`unit_price_cop`) + problemas por ítem inválido."""

    items: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def subtotal_cop(self) -> int:
        return sum(int(it["unit_price_cop"]) * int(it["quantity"]) for it in self.items)


def _tokens(text: str) -> tuple[str, ...]:
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    ).casefold()
    return tuple(sorted(t for t in _TOKEN_RE.split(folded) if t))


def _variant_candidates(variant: Any) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = [_tokens(str(getattr(variant, "title", "") or ""))]
    options = getattr(variant, "options", None) or {}
    values = [str(v) for v in options.values()]
    if values:
        out.append(_tokens(" ".join(values)))
        out.extend(_tokens(v) for v in values)
    return [c for c in out if c]


def _cop_amount(variant: Any) -> int | None:
    prices = list(getattr(variant, "prices", None) or [])
    chosen = next((p for p in prices if str(p.currency_code).lower() == "cop"), None)
    if chosen is None and prices:
        chosen = prices[0]
    if chosen is None:
        return None
    try:
        return int(Decimal(str(chosen.amount)).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _resolve_variant(product: Any, label: str | None) -> tuple[Any | None, bool]:
    variants = list(getattr(product, "variants", None) or [])
    if not variants:
        return None, False
    wanted = _tokens(label or "")
    if wanted:
        for v in variants:
            if wanted in _variant_candidates(v):
                return v, True
    # sin etiqueta o sin match: la primera variante (los precios suelen ser
    # uniformes por producto); `variant_resolved=False` lo hace visible.
    return variants[0], len(variants) == 1 and not wanted


def price_order_items(
    products_by_handle: dict[str, Any], items: list[dict[str, Any]]
) -> PricedOrder:
    priced: list[dict[str, Any]] = []
    problems: list[str] = []
    for raw in items:
        handle = str(raw.get("handle") or "").strip()
        product = products_by_handle.get(handle)
        if product is None:
            problems.append(f"unknown_product:{handle}")
            continue
        try:
            quantity = int(raw.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0
        if quantity < 1:
            problems.append(f"invalid_quantity:{handle}")
            continue
        label = raw.get("variant_label")
        label = str(label).strip() if label is not None else None
        variant, resolved = _resolve_variant(product, label)
        unit_price = _cop_amount(variant) if variant is not None else None
        if unit_price is None:
            problems.append(f"no_price:{handle}")
            continue
        priced.append(
            {
                "handle": handle,
                "variant_label": label or None,
                "quantity": quantity,
                "unit_price_cop": unit_price,
                "title": str(getattr(product, "title", "") or handle),
                "variant_resolved": bool(resolved),
            }
        )
    if problems:
        return PricedOrder(items=[], problems=problems)
    return PricedOrder(items=priced, problems=[])
