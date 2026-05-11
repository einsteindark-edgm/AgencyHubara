"""CatalogPort — Protocol que tools y activities consumen.

Implementaciones existentes:
  - LocalSnapshotCatalogClient (default, lee del snapshot del filesystem).

Futuro:
  - HttpLiveCatalogClient (consultas en vivo a Medusa para stock real-time).
  - MeilisearchCatalogClient (busqueda full-text con scoring).

R-DIP: el Protocol vive aqui; los consumers (tools, activities) reciben
una instancia via constructor injection. No conocen la implementacion.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.platform.catalog.dtos import CatalogProductDTO, SearchResult


@runtime_checkable
class CatalogPort(Protocol):
    async def search(self, q: str, *, limit: int = 10) -> SearchResult: ...
    async def get_by_handle(self, handle: str) -> CatalogProductDTO: ...
