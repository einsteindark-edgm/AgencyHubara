"""Identidad estable de producto para los canales externos (2026-09-14).

Meta Catalog, el feed de Google, la web y el bot identifican cada ítem por el
**SKU** de la variante y agrupan por el **handle** del producto. Nunca por los
ids de Medusa: `prod_…` y `variant_…` se regeneran cuando un producto se borra y
se vuelve a crear (pasó 3 veces con productos y 4 con variantes), y cada vez el
ítem quedó duplicado en Meta y huérfano para el bot.

Ventana de migración: mientras el uploader no haya cargado los SKUs, el id de
Medusa sigue siendo la llave vigente en Meta. Dropear el ítem vaciaría el
catálogo de WhatsApp; conservar la llave vieja es el mal menor, y el caller
lo loguea para que el operador lo vea.
"""
from __future__ import annotations

from .dtos import CatalogProductDTO, CatalogVariantDTO


def has_real_variants(product: CatalogProductDTO) -> bool:
    """Options reales + 2 o más variantes: Meta tiene un ítem POR variante."""
    return bool(product.options) and len(product.variants or []) > 1


def variant_retailer_id(variant: CatalogVariantDTO) -> str:
    """`retailer_id` de una variante en Meta: su SKU, o el id de Medusa si no tiene."""
    return variant.sku or variant.id


def product_retailer_id(product: CatalogProductDTO) -> str:
    """`retailer_id` VIGENTE del producto en Meta.

    Producto con variantes reales: el de la PRIMERA variante (determinista;
    el ítem a nivel producto no existe en Meta). Producto simple: el SKU de su
    única variante, o `product.id` mientras no tenga SKU.
    """
    if has_real_variants(product):
        return variant_retailer_id(product.variants[0])
    first = product.variants[0] if product.variants else None
    if first is not None and first.sku:
        return first.sku
    return product.id


def product_has_sku(product: CatalogProductDTO) -> bool:
    return bool(product.variants) and bool(product.variants[0].sku)
