"""Pull del catalogo desde Medusa, mapeo a CatalogProductDTO, serializacion a JSON.

Use case puro — NO conoce Temporal. Se testea con un fake de
MedusaProductService.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from src.plugins.catalog.agent.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.catalog.dtos import (
    CatalogDimensionsDTO,
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.medusa.models import MedusaCollection, MedusaProduct
from src.platform.medusa.service import MedusaProductService

logger = logging.getLogger(__name__)


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
        tags=_without_foreign_color_tags([t.value for t in mp.tags], product_options),
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
        dimensions=_dimensions(mp),
    )


#: Títulos de la opción de Medusa que es el color del producto (los mismos
#: que lee el bot en `chats/.../product_options.py`).
_COLOR_OPTION_TITLES = frozenset({"color", "colores"})
_COLOR_TAG = "color:"


def _key(value: str) -> str:
    return " ".join(str(value).lower().split())


def _without_foreign_color_tags(
    tags: list[str], options: dict[str, list[str]] | None
) -> list[str]:
    """Con una opción «Color» real (dos valores o más), una etiqueta «Color:»
    que no es uno de sus valores es de plantilla y no entra al snapshot
    (2026-10-17/18: las cajas de velas de deseos con 11 colores de los
    velones). Sin esa opción, las etiquetas son el color del producto."""
    real = next(
        (values for title, values in (options or {}).items()
         if _key(title) in _COLOR_OPTION_TITLES and len(values) >= 2),
        None,
    )
    if real is None:
        return tags
    allowed = {_key(v) for v in real}
    kept = [
        t for t in tags
        if not _key(t).startswith(_COLOR_TAG) or _key(t)[len(_COLOR_TAG):].strip() in allowed
    ]
    if len(kept) != len(tags):
        logger.warning(
            "catalog_sync.color_tags_ignored: %d etiquetas «Color:» contradicen la opción Color (%s)",
            len(tags) - len(kept), ", ".join(real),
        )
    return kept


def _dimensions(mp: MedusaProduct) -> CatalogDimensionsDTO | None:
    """Medidas con la misma regla que la web (`getProductDimensions`): las
    del producto; si no trae ninguna, las de la primera variante que tenga.
    0 = sin dato (la web lo pinta como "0 cm"; al cliente no se le dice)."""
    for source in (mp, *mp.variants):
        dims = CatalogDimensionsDTO(
            height_cm=_measure(source.height),
            width_cm=_measure(source.width),
            length_cm=_measure(source.length),
            weight_g=_measure(source.weight),
        )
        if dims != CatalogDimensionsDTO():
            return dims
    return None


def _measure(value: float | None) -> float | None:
    return value if value else None
