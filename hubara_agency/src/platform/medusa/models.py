"""Pydantic v2 models que reflejan la respuesta de Medusa Admin API.

Estos modelos NO cruzan workflow boundaries (R-JSON los prohibe). Quien
los consume desde una activity los convierte a los `@dataclass` de
`src/platform/catalog/dtos.py` (HU-02) antes de retornar.

Campos extras (Medusa puede añadir nuevos) se ignoran silenciosamente
gracias a `model_config = ConfigDict(extra="ignore")`.

`amount` se preserva como `Decimal` — en Medusa v2 los precios estan en
unidad MAYOR (49.99 = $49.99, NO 4999 centavos). Pydantic v2 acepta
number→Decimal y str→Decimal sin perdida.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MedusaPrice(_Base):
    id: str
    amount: Decimal
    currency_code: str
    min_quantity: int | None = None
    max_quantity: int | None = None
    price_list_id: str | None = None
    rules: dict[str, Any] = Field(default_factory=dict)


class MedusaOptionValue(_Base):
    id: str
    value: str


class MedusaVariant(_Base):
    id: str
    title: str
    sku: str | None = None
    manage_inventory: bool = False
    allow_backorder: bool = False
    prices: list[MedusaPrice] = Field(default_factory=list)
    options: list[MedusaOptionValue] = Field(default_factory=list)


class MedusaOption(_Base):
    id: str
    title: str
    values: list[MedusaOptionValue] = Field(default_factory=list)


class MedusaImage(_Base):
    id: str
    url: str
    rank: int = 0


class MedusaTag(_Base):
    id: str
    value: str


class MedusaCategory(_Base):
    id: str
    name: str
    handle: str | None = None
    parent_category_id: str | None = None


class MedusaCollection(_Base):
    id: str
    title: str
    handle: str | None = None


class MedusaSalesChannel(_Base):
    id: str
    name: str


class MedusaProduct(_Base):
    id: str
    title: str
    handle: str
    description: str | None = None
    status: str
    thumbnail: str | None = None
    height: float | None = None
    width: float | None = None
    length: float | None = None
    weight: float | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    variants: list[MedusaVariant] = Field(default_factory=list)
    options: list[MedusaOption] = Field(default_factory=list)
    images: list[MedusaImage] = Field(default_factory=list)
    tags: list[MedusaTag] = Field(default_factory=list)
    categories: list[MedusaCategory] = Field(default_factory=list)
    collection: MedusaCollection | None = None
    sales_channels: list[MedusaSalesChannel] = Field(default_factory=list)


class MedusaProductPage(_Base):
    products: list[MedusaProduct]
    count: int
    offset: int
    limit: int
