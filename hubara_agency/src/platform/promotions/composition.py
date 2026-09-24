"""Composición de promociones: lecturas (`PromotionsPort`, Medusa o Null) y
comandos de la central de cupones (`PromotionsAdminPort`)."""
from __future__ import annotations

import logging
from functools import lru_cache

from src.platform.medusa.composition import get_medusa_client, get_medusa_settings
from src.platform.promotions.admin import (
    MedusaPromotionsAdmin,
    PromotionsAdminPort,
    UnavailablePromotionsAdmin,
)
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


def _invalidate_local_reader() -> None:
    invalidate = getattr(get_promotions_port(), "invalidate", None)
    if callable(invalidate):
        invalidate()


@lru_cache(maxsize=1)
def get_promotions_admin_port() -> PromotionsAdminPort:
    """Comandos de la central. Sin Medusa NO hay un Null silencioso: cada
    operación levanta `PromotionsUnavailableError` (la API responde 503)."""
    try:
        settings = get_medusa_settings()
    except Exception as exc:  # noqa: BLE001 — sin MEDUSA_* no hay central
        log.warning("PromotionsAdminPort = Unavailable (Medusa sin configurar: %s)", exc)
        return UnavailablePromotionsAdmin()
    if not getattr(settings, "base_url", None):
        return UnavailablePromotionsAdmin()
    return MedusaPromotionsAdmin(get_medusa_client(), on_change=_invalidate_local_reader)


__all__ = ["get_promotions_admin_port", "get_promotions_port"]
