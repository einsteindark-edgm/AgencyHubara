"""Pull del catalogo desde Medusa, mapeo a CatalogProductDTO, serializacion a JSON.

Use case puro — NO conoce Temporal. Se testea con un fake de
MedusaProductService.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from src.plugins.catalog.agent.contracts import CatalogSyncInput, PullCatalogResult
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
    # optval_id → título de la option dueña. Los option values de cada
    # variante llegan con id+value pero sin el título del eje; el id-match
    # contra product.options lo recupera sin tocar los modelos pydantic.
    optval_to_option: dict[str, str] = {
        val.id: opt.title for opt in mp.options for val in opt.values
    }
    product_options: dict[str, list[str]] | None = (
        {opt.title: [val.value for val in opt.values] for opt in mp.options}
        or None
    )

    def _variant_options(v) -> dict[str, str] | None:
        mapped = {
            optval_to_option[ov.id]: ov.value
            for ov in v.options
            if ov.id in optval_to_option
        }
        return mapped or None

    return CatalogProductDTO(
        id=mp.id,
        handle=mp.handle,
        title=mp.title,
        status=mp.status,
        description=mp.description,
        thumbnail=mp.thumbnail,
        options=product_options,
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
                options=_variant_options(v),
            )
            for v in mp.variants
        ],
        images=[CatalogImageDTO(url=i.url, rank=i.rank) for i in mp.images],
        tags=[t.value for t in mp.tags],
        categories=[c.handle or c.name for c in mp.categories],
        # slug → nombre real. El slug es lo que matchea el resolver; el nombre
        # es lo que se le muestra al cliente ("Velas Aromáticas", con tilde).
        category_labels=(
            {(c.handle or c.name): c.name for c in mp.categories} or None
        ),
        metadata=(
            {
                k: (json.dumps(v) if not isinstance(v, str) else v)
                for k, v in (mp.metadata or {}).items()
            }
            or None
        ),
    )
