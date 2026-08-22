"""Pull del catalogo desde Medusa, mapeo a CatalogProductDTO, serializacion a JSON.

Use case puro — NO conoce Temporal. Se testea con un fake de
MedusaProductService.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from src.plugins.catalog.agent.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.medusa.models import MedusaCollection, MedusaProduct
from src.platform.medusa.service import MedusaProductService


# Collections de Medusa cuyos productos SI entran al catalogo conversacional.
# Son las vitrinas del home del storefront: agrupan productos reales y
# vendibles, destacados por el operador. Cualquier otra collection
# ("Story showcase", "Promociones") agrupa contenido armado del sitio y queda
# fuera. Allowlist explicito: collection desconocida → fuera.
DEFAULT_ALLOWED_COLLECTION_HANDLES = frozenset({"home_banner", "home_new_arrivals"})


class PullCatalogUseCase:
    def __init__(
        self,
        medusa_service: MedusaProductService,
        allowed_collection_handles: Iterable[str] | None = None,
    ) -> None:
        self._medusa = medusa_service
        self._allowed_collections = frozenset(
            h.strip().lower()
            for h in (
                DEFAULT_ALLOWED_COLLECTION_HANDLES
                if allowed_collection_handles is None
                else allowed_collection_handles
            )
        )

    async def execute(self, input: CatalogSyncInput) -> PullCatalogResult:
        products: list[CatalogProductDTO] = []
        skipped_with_collection = 0
        # status=published filtra borradores. iter_products pagina transparente.
        async for raw in self._medusa.client.iter_products(
            page_size=100, status="published",
        ):
            mp = MedusaProduct.model_validate(raw)
            # Regla de negocio Hubara: un producto entra si NO tiene collection,
            # o si su collection esta en el allowlist de vitrinas del home.
            # Hasta 2026-08-21 se descartaba TODO producto con collection
            # asignada, asumiendo que las collections eran data de prueba. En la
            # Medusa real las vitrinas del home agrupan productos reales:
            # destacar uno en la web lo sacaba del catalogo conversacional.
            # Costo medido: 8 de 27 productos publicados invisibles para el
            # agente (Duo Zodiacal, Velon Cisne, Angel, Cubo de corazon...).
            if mp.collection is not None and not self._is_allowed(mp.collection):
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

    def _is_allowed(self, collection: MedusaCollection) -> bool:
        handle = (collection.handle or "").strip().lower()
        return handle in self._allowed_collections


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
