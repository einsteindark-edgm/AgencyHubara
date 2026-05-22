"""Puerto del Meta Catalog (Protocol).

Implementaciones:
  * `MetaCatalogClient` — Graph API real
  * `FakeMetaCatalogClient` — para tests
"""
from __future__ import annotations

from typing import Protocol

from src.platform.meta_catalog.dtos import (
    MetaBatchRequest,
    MetaBatchResult,
)


class MetaCatalogPort(Protocol):
    """Puerto del catálogo Meta."""

    name: str

    async def upsert_batch(self, request: MetaBatchRequest) -> MetaBatchResult:
        """Sube/borra un batch al `/items_batch`. Idempotente:
        retailer_ids repetidos son safe (Meta hace upsert)."""
        ...

    async def check_batch_status(
        self,
        catalog_id: str,
        handle: str,
        access_token: str,
    ) -> MetaBatchResult:
        """Polls `/check_batch_request_status?handle=...` y devuelve los
        resultados por ítem. Si Meta dice 'in_progress', devuelve `ok=False`
        con `error='in_progress'`."""
        ...
