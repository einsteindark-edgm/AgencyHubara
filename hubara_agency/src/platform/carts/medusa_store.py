"""MedusaStoreCartReader — adapter del WebCartReaderPort sobre Store API v2.

Semántica (contract suite en `tests/platform/carts/`):
  * 200 → parse TOLERANTE de `{"cart": {...}}`: items malformados (sin
    `product_title` o `quantity` inválida) se descartan sin romper; cart sin
    items es un snapshot válido con `items=()`.
  * no-2xx (404/500/lo que sea) → `None` — para el caller "no hay cart".
  * Errores de transporte httpx (connect/timeout) PROPAGAN — regla 4 del
    ConnectorKit: un timeout NO se disfraza de "cart inexistente"; el caller
    (ingest de sales) decide degradar.

El timeout default es CORTO (2.5s) a propósito: la hidratación corre inline
en el webhook del ingest (proceso API) y no puede retrasar el primer turno
(patrón L-2: timeout corto + degradar).
"""
from __future__ import annotations

import httpx
from loguru import logger

from src.platform.carts.port import WebCartItem, WebCartSnapshot


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_items(raw_items: object) -> tuple[WebCartItem, ...]:
    items: list[WebCartItem] = []
    if not isinstance(raw_items, list):
        return ()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = _str_or_none(raw.get("product_title"))
        try:
            quantity = int(raw.get("quantity"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not title or quantity < 1:
            continue
        unit_price_raw = raw.get("unit_price")
        try:
            unit_price = None if unit_price_raw is None else float(unit_price_raw)
        except (TypeError, ValueError):
            unit_price = None
        items.append(
            WebCartItem(
                product_title=title,
                quantity=quantity,
                product_handle=_str_or_none(raw.get("product_handle")),
                variant_title=_str_or_none(raw.get("variant_title")),
                unit_price=unit_price,
            )
        )
    return tuple(items)


def _parse_cart(cart_id: str, cart: dict) -> WebCartSnapshot:
    shipping = cart.get("shipping_address")
    shipping = shipping if isinstance(shipping, dict) else {}

    address_parts = [
        p
        for p in (
            _str_or_none(shipping.get("address_1")),
            _str_or_none(shipping.get("address_2")),
        )
        if p
    ]
    name_parts = [
        p
        for p in (
            _str_or_none(shipping.get("first_name")),
            _str_or_none(shipping.get("last_name")),
        )
        if p
    ]

    return WebCartSnapshot(
        cart_id=_str_or_none(cart.get("id")) or cart_id,
        items=_parse_items(cart.get("items")),
        email=_str_or_none(cart.get("email")),
        phone=_str_or_none(shipping.get("phone")),
        city=_str_or_none(shipping.get("city")),
        address=", ".join(address_parts) if address_parts else None,
        customer_name=" ".join(name_parts) if name_parts else None,
        currency_code=_str_or_none(cart.get("currency_code")),
    )


class MedusaStoreCartReader:
    """Lee `GET /store/carts/{id}` con `x-publishable-api-key`."""

    def __init__(
        self,
        *,
        base_url: str,
        publishable_api_key: str,
        timeout_s: float = 2.5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = publishable_api_key
        self._timeout_s = timeout_s

    async def get_cart(self, cart_id: str) -> WebCartSnapshot | None:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_s
        ) as client:
            response = await client.get(
                f"/store/carts/{cart_id}",
                headers={"x-publishable-api-key": self._key},
            )

        if response.status_code // 100 != 2:
            logger.info(
                "🛒 [web_cart] Store API {} para {} → sin cart",
                response.status_code,
                cart_id,
            )
            return None

        try:
            payload = response.json() or {}
            cart = payload.get("cart") or {}
            return _parse_cart(cart_id, cart)
        except (ValueError, AttributeError) as exc:
            # Body 200 pero ilegible: para el caller es "no hay cart".
            logger.warning("🛒 [web_cart] body ilegible para {}: {}", cart_id, exc)
            return None
