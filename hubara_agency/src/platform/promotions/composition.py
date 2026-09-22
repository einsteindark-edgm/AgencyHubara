"""Composición del `PromotionsPort`: Medusa si está configurado, si no Null."""
from __future__ import annotations

import logging
from functools import lru_cache

from src.platform.medusa.composition import get_medusa_client, get_medusa_settings
from src.platform.promotions.medusa import MedusaPromotionsPort
from src.platform.promotions.port import NullPromotionsPort, PromotionsPort

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_promotions_port() -> PromotionsPort:
    try:
        settings = get_medusa_settings()
    except Exception as exc:  # noqa: BLE001 — sin MEDUSA_* no hay cupones
        log.warning("PromotionsPort = Null (Medusa sin configurar: %s)", exc)
        return NullPromotionsPort()
    if not getattr(settings, "base_url", None):
        log.warning("PromotionsPort = Null (MEDUSA_BASE_URL vacío)")
        return NullPromotionsPort()
    return MedusaPromotionsPort(get_medusa_client())


__all__ = ["get_promotions_port"]
