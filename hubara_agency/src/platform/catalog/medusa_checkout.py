"""MedusaCheckoutVerification — adapter live del `CheckoutVerificationPort`.

Hace una sola query `list_products(handle=...)` por cada item y compara el
precio live contra el del snapshot. Sin cache: la verdad la define Medusa
en este instante.

NO cruzo workflow boundary directo — el caller es una `tool` que vive en una
activity (`execute_tool`), asi que httpx + Pydantic estan permitidos. Los
DTOs que devuelve son `@dataclass(frozen=True)` JSON-safe.

Errores:
  - Cualquier excepcion no esperada del cliente Medusa -> `catalog_available=False`
    con `error_detail` (el LLM debe escalar segun regla 13 del plan).
  - Producto no encontrado en Medusa -> `in_stock=False`, `live_price=None`,
    `discrepancy=True` (cliente debe ser informado y/o escalar).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.platform.catalog.checkout_port import (
    CheckoutItem,
    CheckoutVerification,
    VerifiedItem,
)
from src.platform.catalog.port import CatalogPort
from src.platform.catalog.errors import ProductNotFoundError, CatalogUnavailableError
from src.platform.medusa.service import MedusaProductService

log = logging.getLogger(__name__)


class MedusaCheckoutVerification:
    """Implementacion live del CheckoutVerificationPort.

    Necesita el `MedusaProductService` (live) y el `CatalogPort` (snapshot)
    para reportar el `snapshot_price` que el LLM le habia mostrado al cliente.
    """

    def __init__(
        self,
        medusa: MedusaProductService,
        snapshot: CatalogPort,
    ) -> None:
        self._medusa = medusa
        self._snapshot = snapshot

    async def verify_items(
        self, items: list[CheckoutItem]
    ) -> CheckoutVerification:
        verified_items: list[VerifiedItem] = []
        any_discrepancy = False

        for it in items:
            try:
                snap_product = await self._snapshot.get_by_handle(it.handle)
                snap_price, snap_currency = _first_price(snap_product)
            except ProductNotFoundError:
                snap_price, snap_currency = (None, None)
            except CatalogUnavailableError as e:
                # Snapshot caido: no podemos comparar. Es un error operativo
                # local, no de Medusa — escalamos.
                log.error("snapshot unavailable during checkout: %s", e)
                return CheckoutVerification(
                    verified=False,
                    catalog_available=False,
                    items=[],
                    error_detail=f"snapshot_unavailable: {e}",
                )

            try:
                page = await self._medusa.list(handle=it.handle, limit=1)
            except Exception as e:  # cubre httpx errors, auth fail, timeouts, etc.
                log.error("medusa live query failed for handle=%s: %s", it.handle, e)
                return CheckoutVerification(
                    verified=False,
                    catalog_available=False,
                    items=[],
                    error_detail=f"medusa_unavailable: {e}",
                )

            if not page.products:
                # Handle no existe en Medusa live (renombrado, despublicado).
                verified_items.append(
                    VerifiedItem(
                        handle=it.handle,
                        title=snap_product.title if snap_price is not None else it.handle,
                        snapshot_price=snap_price,
                        live_price=None,
                        currency=snap_currency,
                        in_stock=False,
                        discrepancy=True,
                    )
                )
                any_discrepancy = True
                continue

            live_p = page.products[0]
            live_price, live_currency = _first_live_price(live_p)
            currency = live_currency or snap_currency

            discrepancy = _prices_differ(snap_price, live_price)
            if discrepancy:
                any_discrepancy = True

            verified_items.append(
                VerifiedItem(
                    handle=it.handle,
                    title=live_p.title,
                    snapshot_price=snap_price,
                    live_price=live_price,
                    currency=currency,
                    # v1: Medusa devuelve manage_inventory + allow_backorder
                    # por variant; v2 si necesitamos stock real-time agregamos
                    # campo. Por ahora asumimos True si el producto existe.
                    in_stock=True,
                    discrepancy=discrepancy,
                )
            )

        return CheckoutVerification(
            verified=not any_discrepancy,
            catalog_available=True,
            items=verified_items,
            error_detail=None,
        )


def _first_price(p: Any) -> tuple[str | None, str | None]:
    if not p.variants:
        return (None, None)
    v = p.variants[0]
    if not v.prices:
        return (None, None)
    return (v.prices[0].amount, v.prices[0].currency_code)


def _first_live_price(p: Any) -> tuple[str | None, str | None]:
    if not p.variants:
        return (None, None)
    v = p.variants[0]
    if not v.prices:
        return (None, None)
    pr = v.prices[0]
    # MedusaPrice.amount es Decimal; serializamos a str para igualar el
    # shape del snapshot (CatalogPriceDTO.amount: str).
    return (str(pr.amount), pr.currency_code)


def _prices_differ(snap: str | None, live: str | None) -> bool:
    """Compara como Decimal para tolerar formatos "29000" vs "29000.00"."""
    if snap is None and live is None:
        return False
    if snap is None or live is None:
        return True
    try:
        return Decimal(snap) != Decimal(live)
    except Exception:
        return snap != live
