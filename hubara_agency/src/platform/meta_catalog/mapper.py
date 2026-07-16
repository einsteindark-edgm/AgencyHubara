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

Imágenes (anti-bug sesión 71f479f7 portado a Meta Catalog):
  * Meta Commerce Catalog SOLO acepta `image/jpeg` y `image/png`. Los `.webp`
    son aceptados por la API con 200 OK pero quedan visualmente vacíos en
    Commerce Manager y en las cards `interactive.product`.
  * Las URLs del catálogo Hubara apuntan a `assets.hubara.com.co/...webp`.
  * Usamos `normalize_image_url()` (que vive en `platform/whatsapp/` porque
    ahí nació, pero la restricción del formato es Meta-global — aplica a
    Catalog también) para wrappear las URLs con Cloudflare Image Resizing
    (`/cdn-cgi/image/format=jpeg/`). El push reusa **la URL que ya quedó
    persistida en el snapshot del pull anterior** — NO re-consulta Medusa.
  * Comparación de duplicados (`additional` vs `image_url`): se hace ANTES
    de normalizar, contra la URL cruda del snapshot. Sino, dos URLs que
    deduplicarian al normalizarse podrian quedar como duplicadas.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Optional

from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO
from src.platform.catalog.image_labels import derive_image_label
from src.platform.meta_catalog.dtos import MetaCatalogItem
from src.platform.whatsapp.media_url import normalize_image_url

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
    # Pick primary image (UN-normalized — para deduplicar el additional set).
    primary_raw = product.thumbnail
    if not primary_raw and product.images:
        primary_raw = product.images[0].url
    if not primary_raw:
        return None

    price = _first_price_meta_format(product)
    if not price:
        return None  # sin precio no se puede sincronizar

    availability = "in stock" if product.status == "published" else "out of stock"

    description = product.description or product.title

    # Additional images (UN-normalized — para dedupe contra primary_raw).
    additional_raw: list[str] = []
    if product.images:
        for img in product.images[1:11]:  # Meta acepta hasta 10
            if img.url and img.url != primary_raw:
                additional_raw.append(img.url)

    # Normalize a la frontera con Meta — wrappea .webp con Cloudflare Image
    # Resizing si la URL viene de un host conocido (assets.hubara.com.co).
    # NOTA: el snapshot del pull anterior ya quedó persistido; estamos
    # transformando solo el formato de entrega a Meta, no re-fetcheando.
    image_url = normalize_image_url(primary_raw)
    additional_images = [normalize_image_url(u) for u in additional_raw]

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
            continue
        variant_items = _variant_items(p, m)
        if variant_items:
            mapped.extend(variant_items)
        else:
            mapped.append(m)
    return mapped, skipped


def _variant_items(
    product: CatalogProductDTO, base: MetaCatalogItem
) -> list[MetaCatalogItem]:
    """Un item por variante cuando el producto tiene variantes REALES.

    Caso Duo Zodiacal v2 (2026-07-15): option "Signo" + 12 variantes con la
    foto de cada signo nombrada por su valor. Cada variante va a Meta como
    item propio (`retailer_id = variant_id`) agrupado por `item_group_id =
    product_id`, con SU imagen (match label↔option value por filename) y su
    precio. Productos legacy (sin options / variante única) → lista vacía y
    el caller publica el item único de siempre.
    """
    if not product.options or len(product.variants) < 2:
        return []
    label_to_url: dict[str, str] = {}
    for img in product.images:
        label = derive_image_label(img.url)
        if label and label.lower() not in label_to_url:
            label_to_url[label.lower()] = img.url

    items: list[MetaCatalogItem] = []
    for v in product.variants:
        price = _price_meta_format(v.prices)
        if not price:
            continue
        raw_img: str | None = None
        for candidate in (v.title, *(v.options or {}).values()):
            if candidate and candidate.lower() in label_to_url:
                raw_img = label_to_url[candidate.lower()]
                break
        items.append(
            replace(
                base,
                retailer_id=v.id,
                item_group_id=product.id,
                name=f"{product.title} · {v.title}"[:200],
                price=price,
                image_url=(
                    normalize_image_url(raw_img) if raw_img else base.image_url
                ),
                # La card de la variante muestra SU diseño; las fotos de los
                # demás signos confunden como "additional".
                additional_image_urls=[],
                gtin=(
                    v.sku
                    if v.sku and _GTIN13_RE.match(v.sku)
                    else None
                ),
            )
        )
    return items


# =============================================================================
# Helpers
# =============================================================================


def _first_price_meta_format(
    product: CatalogProductDTO, *, preferred_currency: str = "COP"
) -> Optional[str]:
    """Meta espera el precio como string 'AMOUNT CURRENCY', sin separadores
    de miles. Ej: '23000 COP', '46500 COP'.

    PREFIERE el precio en `preferred_currency` (Hubara=COP). Medusa devuelve
    varios precios por variante (ej. `usd` + `cop` con el mismo amount) y tomar
    `prices[0]` a ciegas publicaba USD en la card de WhatsApp (prod 2026-06-30:
    "35000 USD" en vez de "35000 COP"). Fallback al primer precio disponible si
    no hay match en la moneda preferida (edge case).

    Si el amount viene con decimales (raro en COP), los preservamos.
    """
    if not product.variants:
        return None
    return _price_meta_format(
        product.variants[0].prices, preferred_currency=preferred_currency
    )


def _price_meta_format(
    prices: list[CatalogPriceDTO], *, preferred_currency: str = "COP"
) -> Optional[str]:
    """Formato Meta para una lista de precios (COP-first, ver docstring de
    `_first_price_meta_format`)."""
    if not prices:
        return None
    pref = preferred_currency.lower()
    p = next(
        (pr for pr in prices if (pr.currency_code or "").lower() == pref),
        prices[0],
    )
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
