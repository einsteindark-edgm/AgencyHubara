"""Tarjetas del carrusel de una campaña — el ÚNICO I/O del carrusel.

Product cards de Meta: cada handle se resuelve en el catálogo (identidad
vigente = `product_retailer_id`, el mismo ítem que el sync publica en el
catálogo de Meta `META_CATALOG_ID` conectado al número). Foto y precio los
pone Meta; acá no se sube nada. La decisión de qué va en cada tarjeta es del
dominio puro (`build_carousel_cards`).
"""
from __future__ import annotations

import os
from typing import Any

from src.plugins.marketing.domain.campaigns import (
    build_carousel_cards,
    carousel_handles,
)
from src.sdk.catalogkit import ProductNotFoundError
from src.sdk.connectorkit import get_catalog_client
from src.sdk.messagingkit import CarouselCard


class CarouselError(RuntimeError):
    """El carrusel no se puede armar (producto fuera del catálogo, config)."""


def meta_catalog_id() -> str:
    """Catálogo de Meta conectado al número del negocio (el mismo que usa el
    bot para `product_list`)."""
    return (os.environ.get("META_CATALOG_ID") or "").strip()


async def resolve_campaign_carousel(
    campaign: dict[str, Any], *, now_ms: int | None = None
) -> list[CarouselCard]:
    """Tarjetas listas para enviar. `[]` si la campaña no lleva carrusel."""
    handles = carousel_handles(campaign)
    if not handles:
        return []
    catalog_id = meta_catalog_id()
    if not catalog_id:
        raise CarouselError(
            "META_CATALOG_ID no configurado — el carrusel usa el catálogo de Meta"
        )
    catalog = get_catalog_client()
    products: dict[str, Any] = {}
    for handle in handles:
        try:
            products[handle] = await catalog.get_by_handle(handle)
        except ProductNotFoundError as e:
            raise CarouselError(f"producto {handle!r} ya no está en el catálogo") from e
    try:
        return build_carousel_cards(handles, products, catalog_id=catalog_id)
    except ValueError as e:
        raise CarouselError(str(e)) from e


__all__ = ["CarouselError", "meta_catalog_id", "resolve_campaign_carousel"]
