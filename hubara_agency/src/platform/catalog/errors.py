"""Errores tipados del CatalogPort."""
from __future__ import annotations


class CatalogError(Exception):
    """Base para errores del catalog port."""


class ProductNotFoundError(CatalogError):
    def __init__(self, handle: str) -> None:
        self.handle = handle
        super().__init__(f"Product handle not found: {handle!r}")


class CatalogUnavailableError(CatalogError):
    """El snapshot no esta disponible o esta corrupto."""
