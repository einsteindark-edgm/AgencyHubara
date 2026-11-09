"""Ajuste del precio de un pedido al valor real de la venta (precio negociado).

Por qué existe (pedido #169, 2026-11-09): una venta al por mayor se negoció con
el cliente, pero el pedido quedó con los precios de lista y al marcarlo
entregado infló las ventas. Los precios al por mayor se negocian en cada venta,
así que no hay tabla: el operador escribe el precio real de cada línea.

Regla: el precio vive en la línea de Medusa (``unit_price``) — Órdenes,
«Exportar», Ads, el cobro contra entrega y la contabilidad leen ese total.
Nunca un ajuste aparte en metadata (eso solo existe para el envío, porque
Medusa no deja editar un método de envío). La línea guarda el precio de lista
(``list_unit_price_cop``, la misma llave que usa el cupón del pedido #44) y
quién lo cambió y por qué.

Puro: no toca red ni disco. El adapter (`MedusaOrderCommand.adjust_prices`)
aplica el plan con la edición de pedidos de Medusa.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.platform.orders.state import (
    META_KEY_HISTORY,
    META_KEY_PRICE_ADJUSTMENT_REFUND,
    read_stage,
)

#: Llaves de la metadata de la línea ajustada.
LINE_KEY_LIST_PRICE = "list_unit_price_cop"
LINE_KEY_ADJUSTED_REASON = "price_adjusted_reason"
LINE_KEY_ADJUSTED_BY = "price_adjusted_by"
LINE_KEY_ADJUSTED_AT_MS = "price_adjusted_at_ms"


class PriceAdjustmentError(ValueError):
    """El ajuste pedido no es válido (el mensaje se le muestra al operador)."""


@dataclass(frozen=True)
class LinePriceUpdate:
    """Una línea que cambia de precio: lo que se manda a la edición de Medusa."""

    item_id: str
    quantity: int
    unit_price_cop: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _price(raw: Any) -> int | None:
    """COP entero > 0, o None."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _int_cop(raw: Any) -> int:
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return 0


def plan_line_prices(
    items: Sequence[Mapping[str, Any]],
    prices: Mapping[str, int],
    *,
    by: str,
    reason: str,
    now_ms: int,
) -> list[LinePriceUpdate]:
    """Las líneas cuyo precio cambia, con su auditoría.

    ``items``: las líneas del pedido como las devuelve Medusa (``id``,
    ``quantity``, ``unit_price``, ``metadata``). ``prices``: ``{id: precio
    unitario real}``. Una línea con el mismo precio no es un cambio. El precio
    de lista es el de la línea ANTES del primer ajuste (un segundo ajuste lo
    conserva).

    Raises:
        PriceAdjustmentError: sin motivo, una línea que no es del pedido o un
            precio que no es un entero mayor que 0.
    """
    if not reason.strip():
        raise PriceAdjustmentError("Escribe el motivo del ajuste (por ejemplo, el precio negociado).")
    by_id = {str(it.get("id")): it for it in items if it.get("id")}
    updates: list[LinePriceUpdate] = []
    for item_id, raw_price in prices.items():
        line = by_id.get(item_id)
        if line is None:
            raise PriceAdjustmentError(f"{item_id} no es una línea de este pedido.")
        price = _price(raw_price)
        if price is None:
            raise PriceAdjustmentError(
                f"El precio de «{line.get('title') or item_id}» debe ser un entero mayor que 0."
            )
        current = _int_cop(line.get("unit_price"))
        if price == current:
            continue
        raw_meta = line.get("metadata")
        line_meta: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
        list_price = _price(line_meta.get(LINE_KEY_LIST_PRICE)) or current
        updates.append(
            LinePriceUpdate(
                item_id=item_id,
                quantity=int(line.get("quantity") or 0),
                unit_price_cop=price,
                metadata={
                    LINE_KEY_LIST_PRICE: list_price,
                    LINE_KEY_ADJUSTED_REASON: reason.strip(),
                    LINE_KEY_ADJUSTED_BY: by,
                    LINE_KEY_ADJUSTED_AT_MS: now_ms,
                },
            )
        )
    return updates


def build_price_adjustment_patch(
    metadata: Mapping[str, Any] | None,
    *,
    by: str,
    reason: str,
    total_before_cop: int,
    total_after_cop: int,
    refunded_cop: int,
    now_ms: int,
) -> dict[str, Any]:
    """Patch de la metadata del pedido: entrada ``price_adjusted`` en el
    historial (quién, por qué, total antes y después) y, si se reembolsó en
    Medusa la diferencia de un pedido ya cobrado, el acumulado en
    ``META_KEY_PRICE_ADJUSTMENT_REFUND``. No cambia la etapa."""
    current = metadata or {}
    stage = read_stage(dict(current))
    entry: dict[str, Any] = {
        "from": stage,
        "to": stage,
        "at_ms": now_ms,
        "by": by,
        "event": "price_adjusted",
        "note": reason.strip(),
        "total_before_cop": int(total_before_cop),
        "total_after_cop": int(total_after_cop),
    }
    patch: dict[str, Any] = {}
    if refunded_cop > 0:
        entry["refunded_cop"] = int(refunded_cop)
        previous = _price(current.get(META_KEY_PRICE_ADJUSTMENT_REFUND)) or 0
        patch[META_KEY_PRICE_ADJUSTMENT_REFUND] = previous + int(refunded_cop)
    patch[META_KEY_HISTORY] = [*list(current.get(META_KEY_HISTORY) or []), entry]
    return patch
