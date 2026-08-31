"""WebCartReaderPort — contrato de lectura del carrito web.

HU web-cart hot lead: la página web genera un link wa.me con un token
`ref:cart_<id>`; el ingest de sales hidrata el carrito vía este port para
sembrar el order_draft ANTES del primer turno del LLM. La lectura es
best-effort por diseño: `None` = "no disponible" (el caller degrada al flujo
conversacional normal); los errores de transporte PROPAGAN (regla 4 del
ConnectorKit — el adapter no disfraza un timeout de "cart inexistente").

R-DIP: el Protocol vive aquí; los consumers lo reciben por constructor
injection vía `src.sdk.connectorkit` (jamás el adapter vendor directo).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class WebCartError(Exception):
    """Base de los errores del web cart reader."""


class WebCartUnavailableError(WebCartError):
    """La Store API no está disponible/configurada — NO se verificó nada.

    Distinto de `None` (cart VERIFICADO como inexistente, 404 real): el
    caller que recibe esta excepción degrada sin sacar conclusiones sobre
    la existencia del cart (premortem FM-08 — un `None` no verificado
    llevaría a des-clasificar el origin de un lead legítimo).
    """


class WebCartAuthError(WebCartError):
    """401/403 de la Store API — publishable key inválida/revocada/rotada.

    Premortem FM-07: reportarlo como "cart_not_found" haría al operador
    perseguir carts fantasma cuando el fix es rotar la key.
    """


@dataclass(frozen=True)
class WebCartItem:
    """Line item del carrito web (subset tolerante del shape Store API v2)."""

    product_title: str
    quantity: int
    product_handle: str | None = None
    variant_title: str | None = None
    unit_price: float | None = None


@dataclass(frozen=True)
class WebCartSnapshot:
    """Snapshot inmutable del carrito web al momento de la hidratación."""

    cart_id: str
    items: tuple[WebCartItem, ...] = field(default_factory=tuple)
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    address: str | None = None
    customer_name: str | None = None
    currency_code: str | None = None


class WebCartReaderPort(Protocol):
    """Contrato: devuelve el snapshot, `None` SOLO si el cart se verificó
    como inexistente (404 real), y lanza `WebCartUnavailableError` /
    `WebCartAuthError` / errores de transporte cuando NO pudo verificar."""

    async def get_cart(self, cart_id: str) -> WebCartSnapshot | None: ...


class NullWebCartReader:
    """Fallback de composición sin config: lanza `WebCartUnavailableError`.

    Mantiene el flujo degradado por construcción: sin publishable key
    configurada, todo cart ref se trata como no-verificable y el bot vende
    con lo que dice el mensaje (la misión es vender) — sin afirmar jamás
    que el cart "no existe".
    """

    async def get_cart(self, cart_id: str) -> WebCartSnapshot | None:
        raise WebCartUnavailableError("store API no configurada (sin publishable key)")


class FakeWebCartReader:
    """Fake oficial del contrato (regla 2 del ConnectorKit): snapshots
    pre-cargados por cart_id — testear plugins sin red ni credenciales."""

    def __init__(self, snapshots: dict[str, WebCartSnapshot]) -> None:
        self._by_id = dict(snapshots)

    async def get_cart(self, cart_id: str) -> WebCartSnapshot | None:
        return self._by_id.get(cart_id)
