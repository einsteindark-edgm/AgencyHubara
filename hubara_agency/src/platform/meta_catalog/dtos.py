"""DTOs JSON-safe del Meta Commerce Catalog.

R-JSON: frozen dataclasses, primitivos solamente.

Spec Meta: https://developers.facebook.com/docs/marketing-api/catalog/reference
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class MetaCatalogItem:
    """Item del catálogo en formato Meta.

    Mapping CatalogProductDTO → MetaCatalogItem (ver `mapper.py`):

      retailer_id      ← CatalogProductDTO.id
      name             ← CatalogProductDTO.title
      description      ← CatalogProductDTO.description (fallback: title)
      url              ← f"https://hubara.com.co/products/{handle}"
      image_url        ← thumbnail (o images[0].url)
      additional_image_urls ← images[1..10].url
      price            ← variants[0].prices[0].amount + currency (ej: "23000 COP")
      availability     ← "in stock" si status=="published" else "out of stock"
      condition        ← "new" (constante)
      brand            ← "Hubara"
      google_product_category ← (manual mapping)
      custom_label_0..4 ← categories[]
      gtin             ← variants[0].sku si formato GTIN-13 válido
      tags             ← tags[] (custom field)

    Campos requeridos por Meta: retailer_id, name, image_url, price,
    availability, condition, brand, description.
    """

    retailer_id: str
    name: str
    description: str
    url: str
    image_url: str
    price: str  # formato "23000 COP" — amount + space + ISO currency
    availability: Literal["in stock", "out of stock", "preorder", "discontinued"]
    condition: Literal["new", "used", "refurbished"] = "new"
    brand: str = "Hubara"

    # Opcionales pero útiles
    additional_image_urls: list[str] = field(default_factory=list)
    gtin: str | None = None
    google_product_category: str | None = None  # taxonomía Google
    custom_label_0: str | None = None
    custom_label_1: str | None = None
    custom_label_2: str | None = None
    custom_label_3: str | None = None
    custom_label_4: str | None = None
    sale_price: str | None = None  # si hay descuento

    # Custom data (libre)
    custom_data_tags: str | None = None  # JSON-encoded tags array


@dataclass(frozen=True)
class MetaBatchRequest:
    """Pedido de batch upsert/delete al `/items_batch`.

    `creates` + `updates` + `deletes` agrupados internamente.
    Meta procesa en orden — útil para `delete antes de create` con mismo
    retailer_id (caso edge).
    """

    catalog_id: str
    access_token: str
    creates: list[MetaCatalogItem] = field(default_factory=list)
    updates: list[MetaCatalogItem] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)  # retailer_ids
    allow_upsert: bool = True  # si True, CREATE actúa como UPSERT


@dataclass(frozen=True)
class MetaBatchItemResult:
    retailer_id: str
    method: Literal["CREATE", "UPDATE", "DELETE"]
    ok: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class MetaBatchResult:
    """Resultado del batch entero. `handle` es lo que Meta devuelve para
    consultar status async; `items_results` se llena cuando el caller
    polleó `check_batch_request_status`."""

    handle: str | None
    ok: bool
    submitted: int
    error: str | None = None
    items_results: list[MetaBatchItemResult] = field(default_factory=list)
