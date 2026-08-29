"""Composición del web cart reader (selección de adapter por config).

Patrón de `orders/composition.py`: con `MEDUSA_BASE_URL` +
`MEDUSA_PUBLISHABLE_API_KEY` seteadas → adapter real; sin config (o con
settings inválidas — p. ej. el proceso API local sin env MEDUSA_*) →
`NullWebCartReader`, que degrada TODO cart ref al flujo conversacional.
Jamás rompe el boot del proceso: la hidratación es best-effort por diseño.
"""
from __future__ import annotations

from functools import lru_cache

from loguru import logger

from src.platform.carts.medusa_store import MedusaStoreCartReader
from src.platform.carts.port import NullWebCartReader, WebCartReaderPort


@lru_cache(maxsize=1)
def get_web_cart_reader() -> WebCartReaderPort:
    try:
        from src.platform.medusa.composition import get_medusa_settings

        settings = get_medusa_settings()
    except Exception as exc:  # settings inválidas/ausentes → degrade
        logger.warning(
            "🛒 [web_cart] MedusaSettings no disponibles ({}) — "
            "NullWebCartReader (hidratación degradada)",
            type(exc).__name__,
        )
        return NullWebCartReader()

    if settings.publishable_api_key:
        return MedusaStoreCartReader(
            base_url=settings.base_url,
            publishable_api_key=settings.publishable_api_key,
        )

    logger.warning(
        "🛒 [web_cart] MEDUSA_PUBLISHABLE_API_KEY no configurada — "
        "NullWebCartReader (todo cart ref degrada al flujo normal)"
    )
    return NullWebCartReader()
