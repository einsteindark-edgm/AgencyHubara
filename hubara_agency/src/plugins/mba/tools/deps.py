"""Composición de las dependencias de las tools (canal 1: factories del SDK).

Cada port puede no estar configurado en este proceso (local sin Medusa, caja
sin snapshot): la dependencia queda en ``None`` y la tool responde un error
explícito que el agente sabe manejar, en vez de un 500 en un endpoint público.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from loguru import logger

from src.sdk.connectorkit import (
    get_catalog_client,
    get_checkout_verification_port,
    get_order_query_port,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore


@dataclass
class ToolDeps:
    catalog: Any | None
    checkout: Any | None
    order_query: Any | None
    metadata: Any  # FilesystemMetadataStore


def _try(name: str, factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = tool degradada, no proceso caído
        logger.warning("[mba] {} no disponible en este proceso: {}", name, exc)
        return None


@lru_cache(maxsize=1)
def default_deps() -> ToolDeps:
    return ToolDeps(
        catalog=_try("catalog", get_catalog_client),
        checkout=_try("checkout", get_checkout_verification_port),
        order_query=_try("order_query", get_order_query_port),
        metadata=FilesystemMetadataStore(WORKSPACE_VAULT_DIR),
    )
