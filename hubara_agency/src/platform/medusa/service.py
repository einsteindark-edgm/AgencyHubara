"""MedusaProductService — typed wrapper sobre HttpMedusaClient."""
from __future__ import annotations

from typing import Any

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.models import MedusaProduct, MedusaProductPage


class MedusaProductService:
    def __init__(self, client: HttpMedusaClient) -> None:
        self.client = client

    async def get(self, product_id: str) -> MedusaProduct:
        raw = await self.client.get_product(product_id)
        return MedusaProduct.model_validate(raw)

    async def list(self, **kwargs: Any) -> MedusaProductPage:
        raw = await self.client.list_products(**kwargs)
        return MedusaProductPage.model_validate(raw)
