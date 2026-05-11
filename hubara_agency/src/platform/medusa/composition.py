"""DI factories para platform/medusa.

Patron: lru_cache(1) para que el HttpMedusaClient (recurso de larga vida)
sea singleton por proceso. Mismo patron que `src/platform/temporal/client.py`.
"""
from __future__ import annotations

from functools import lru_cache

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings


@lru_cache(maxsize=1)
def get_medusa_settings() -> MedusaSettings:
    return MedusaSettings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_medusa_client() -> HttpMedusaClient:
    s = get_medusa_settings()
    return HttpMedusaClient(
        base_url=s.base_url,
        admin_token=s.admin_token,
        admin_email=s.admin_email,
        admin_password=s.admin_password,
        timeout=s.http_timeout,
    )


@lru_cache(maxsize=1)
def get_medusa_product_service() -> MedusaProductService:
    return MedusaProductService(get_medusa_client())
