"""Mapping `CatalogProductDTO` → `MetaCatalogItem`.

Reglas Hubara (no negociables):
  * `brand = "Hubara"`
  * `condition = "new"`
  * `url` = `https://hubara.com.co/products/{handle}` (configurable por tenant)
  * `availability = "in stock"` si `status == "published"`, else `"out of stock"`
  * `description` fallback al `title` si está vacío (Meta lo requiere)
  * `image_url` toma thumbnail; si no, `images[0].url`. Si tampoco, item se
    skippea (no se puede sincronizar sin imagen).

Categorías:
  * `categories[0]` se mapea a `google_product_category` via diccionario
    estático (ver `_GOOGLE_CATEGORY_MAP`). Sin match → None.
  * `categories[0..4]` se reflejan en `custom_label_0..4`.

GTIN: si `variants[0].sku` tiene 13 dígitos numéricos, se asume GTIN-13. Si no,
se omite (Meta acepta items sin GTIN).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from src.platform.catalog.dtos import CatalogProductDTO
from src.platform.meta_catalog.dtos import MetaCatalogItem

_GTIN13_RE = re.compile(r"^\d{13}$")

# Mapping manual categoría Hubara → Google Product Category (alta nivel).
# Hubara vende velas, no necesita exhaustividad. Si la categoría no está
# en este map, dejamos `google_product_category=None` y Meta no rechaza.
_GOOGLE_CATEGORY_MAP: dict[str, str] = {
    "velas": "Home & Garden > Decor > Home Fragrances > Candles",
    "religiosa": "Home & Garden > Decor > Home Fragrances > Candles",
    "religiosas": "Home & Garden > Decor > Home Fragrances > Candles",
    "aromaticas": "Home & Garden > Decor > Home Fragrances > Candles",
    "aromaticas terapia": "Home & Garden > Decor > Home Fragrances > Candles",
    "decorativas": "Home & Garden > Decor > Home Fragrances > Candles",
    "regalo": "Home & Garden > Decor > Home Fragrances > Candles",
}


def map_product_to_meta(
    product: CatalogProductDTO,
    *,
    site_base_url: str = "https://hubara.com.co",
    brand: str = "Hubara",
) -> MetaCatalogItem | None:
    """Convierte un `CatalogProductDTO` a `MetaCatalogItem`.

    Devuelve None si el producto NO se puede sincronizar (sin imagen,
    sin precio). El caller debe loguear y skip.
    """
    image_url = product.thumbnail
    if not image_url and product.images:
        image_url = product.images[0].url
    if not image_url:
        return None

    price = _first_price_meta_format(product)
    if not price:
        return None  # sin precio no se puede sincronizar

    availability = "in stock" if product.status == "published" else "out of stock"

    description = product.description or product.title

    additional_images: list[str] = []
    if product.images:
        for img in product.images[1:11]:  # Meta acepta hasta 10
            if img.url and img.url != image_url:
                additional_images.append(img.url)

    gtin: str | None = None
    if product.variants:
        sku = product.variants[0].sku or ""
        if sku and _GTIN13_RE.match(sku):
            gtin = sku

    google_cat = _resolve_google_category(product.categories)
    custom_labels = _custom_labels(product.categories)

    tags_json: str | None = None
    if product.tags:
        tags_json = json.dumps(product.tags, ensure_ascii=False)

    return MetaCatalogItem(
        retailer_id=product.id,
        name=product.title[:200],  # Meta limit
        description=description[:9999],
        url=f"{site_base_url.rstrip('/')}/products/{product.handle}",
        image_url=image_url,
        additional_image_urls=additional_images,
        price=price,
        availability=availability,
        condition="new",
        brand=brand,
        gtin=gtin,
        google_product_category=google_cat,
        custom_label_0=custom_labels[0],
        custom_label_1=custom_labels[1],
        custom_label_2=custom_labels[2],
        custom_label_3=custom_labels[3],
        custom_label_4=custom_labels[4],
        custom_data_tags=tags_json,
    )


def map_products_batch(
    products: list[CatalogProductDTO],
    *,
    site_base_url: str = "https://hubara.com.co",
    brand: str = "Hubara",
) -> tuple[list[MetaCatalogItem], list[str]]:
    """Mapea un batch. Devuelve `(mapped, skipped_ids)`.

    `skipped_ids` son los productos que no se pueden sincronizar (sin
    imagen o sin precio). El caller los loguea para auditoría.
    """
    mapped: list[MetaCatalogItem] = []
    skipped: list[str] = []
    for p in products:
        m = map_product_to_meta(p, site_base_url=site_base_url, brand=brand)
        if m is None:
            skipped.append(p.id)
        else:
            mapped.append(m)
    return mapped, skipped


# =============================================================================
# Helpers
# =============================================================================


def _first_price_meta_format(product: CatalogProductDTO) -> Optional[str]:
    """Meta espera el precio como string 'AMOUNT CURRENCY', sin separadores
    de miles. Ej: '23000 COP', '46500 COP'.

    Si el amount viene con decimales (raro en COP), los preservamos.
    """
    if not product.variants:
        return None
    v = product.variants[0]
    if not v.prices:
        return None
    p = v.prices[0]
    if not p.amount or not p.currency_code:
        return None
    # Normaliza: si amount viene como "23000.00" o "23,000", limpiamos
    amount_clean = p.amount.replace(",", "").strip()
    return f"{amount_clean} {p.currency_code.upper()}"


def _resolve_google_category(categories: list[str]) -> str | None:
    for cat in categories:
        key = cat.strip().lower()
        if key in _GOOGLE_CATEGORY_MAP:
            return _GOOGLE_CATEGORY_MAP[key]
    return None


def _custom_labels(categories: list[str]) -> list[str | None]:
    """Devuelve 5 slots (Meta acepta custom_label_0..4)."""
    out: list[str | None] = []
    for i in range(5):
        if i < len(categories):
            out.append(categories[i][:100])  # limit defensivo
        else:
            out.append(None)
    return out
