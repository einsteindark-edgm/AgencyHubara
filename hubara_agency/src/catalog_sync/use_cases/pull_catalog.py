"""Pull del catalogo desde Medusa, mapeo a CatalogProductDTO, serializacion a JSON.

Use case puro — NO conoce Temporal. Se testea con un fake de
MedusaProductService.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from src.catalog_sync.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.medusa.models import MedusaProduct
from src.platform.medusa.service import MedusaProductService


class PullCatalogUseCase:
    def __init__(self, medusa_service: MedusaProductService) -> None:
        self._medusa = medusa_service

    async def execute(self, input: CatalogSyncInput) -> PullCatalogResult:
        products: list[CatalogProductDTO] = []
        skipped_with_collection = 0
        # status=published filtra borradores. iter_products pagina transparente.
        async for raw in self._medusa.client.iter_products(
            page_size=100, status="published",
        ):
            mp = MedusaProduct.model_validate(raw)
            # Regla de negocio Hubara: solo se exponen al agente productos
            # SIN collection asignada. Las collections en Medusa se usan para
            # agrupar productos de prueba / promociones / variantes que NO
            # deben aparecer en el catalogo conversacional. Este filtro
            # tambien limpia los duplicados sucios (cruz-de-vida vs cruz-vida)
            # si alguno tiene collection.
            if mp.collection is not None:
                skipped_with_collection += 1
                continue
            products.append(_to_dto(mp))

        payload = json.dumps([asdict(p) for p in products])
        return PullCatalogResult(
            products_json=payload,
            count=len(products),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_etag=None,  # conditional GET es follow-up
        )


def _to_dto(mp: MedusaProduct) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=mp.id,
        handle=mp.handle,
        title=mp.title,
        status=mp.status,
        description=mp.description,
        thumbnail=mp.thumbnail,
        variants=[
            CatalogVariantDTO(
                id=v.id,
                title=v.title,
                sku=v.sku,
                prices=[
                    CatalogPriceDTO(
                        amount=str(p.amount),  # Decimal → str (R-JSON)
                        currency_code=p.currency_code,
                        min_quantity=p.min_quantity,
                        max_quantity=p.max_quantity,
                    )
                    for p in v.prices
                ],
            )
            for v in mp.variants
        ],
        images=[CatalogImageDTO(url=i.url, rank=i.rank) for i in mp.images],
        tags=[t.value for t in mp.tags],
        categories=[c.handle or c.name for c in mp.categories],
        metadata=(
            {
                k: (json.dumps(v) if not isinstance(v, str) else v)
                for k, v in (mp.metadata or {}).items()
            }
            or None
        ),
    )
