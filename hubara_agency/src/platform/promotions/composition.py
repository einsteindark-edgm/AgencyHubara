"""Composición de promociones: lecturas (`PromotionsPort`, Medusa o Null) y
comandos de la central de cupones (`PromotionsAdminPort`)."""
from __future__ import annotations

import logging
from functools import lru_cache

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.medusa.composition import get_medusa_client, get_medusa_settings
from src.platform.promotions.admin import (
    MedusaPromotionsAdmin,
    PromotionsAdminPort,
    UnavailablePromotionsAdmin,
)
from src.platform.promotions.audit import CouponAuditLog, CouponAuditPort
from src.platform.promotions.coupon_sales import (
    CouponSalesReader,
    UnavailableCouponSalesReader,
)
from src.platform.promotions.medusa import MedusaPromotionsPort
from src.platform.promotions.port import NullPromotionsPort, PromotionsPort
from src.platform.promotions.quota_lock import VaultQuotaLock
from src.platform.promotions.quota_store import PromoQuotaStore, VaultPromoQuotaStore

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


@lru_cache(maxsize=1)
def get_coupon_sales_reader() -> CouponSalesReader | UnavailableCouponSalesReader:
    """Vendidas de cada cupo, derivadas de los pedidos de Medusa. Sin Medusa
    no hay cómo contarlas: el lector levanta `PromotionsUnavailableError` y el
    cupón con cupo NO se aplica (falla cerrada)."""
    try:
        settings = get_medusa_settings()
    except Exception as exc:  # noqa: BLE001
        log.warning("CouponSalesReader = Unavailable (Medusa sin configurar: %s)", exc)
        return UnavailableCouponSalesReader()
    if not getattr(settings, "base_url", None):
        return UnavailableCouponSalesReader()
    return CouponSalesReader(get_medusa_client())


# El cupo, el registro de cambios y los candados viven en el vault. Sin cache:
# son baratos y el vault se resuelve en cada llamada (tests lo aíslan).


def get_promo_quota_store() -> PromoQuotaStore:
    return VaultPromoQuotaStore(WORKSPACE_VAULT_DIR)


def get_coupon_audit_log() -> CouponAuditPort:
    return CouponAuditLog(WORKSPACE_VAULT_DIR)


def get_quota_lock() -> VaultQuotaLock:
    return VaultQuotaLock(WORKSPACE_VAULT_DIR)


__all__ = [
    "get_coupon_audit_log",
    "get_coupon_sales_reader",
    "get_promo_quota_store",
    "get_promotions_admin_port",
    "get_promotions_port",
    "get_quota_lock",
]
