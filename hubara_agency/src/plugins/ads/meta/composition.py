"""Composición de la integración Meta — factories con `@lru_cache` (R-STATELESS).

El vendor real se elige acá. Los tests inyectan fakes monkeypatcheando los
providers de `api/meta_oauth.py`, no estos factories.
"""
from __future__ import annotations

from functools import lru_cache

from src.plugins.ads.meta.client import GraphMetaAds, MetaAdsPort
from src.plugins.ads.meta.settings import meta_settings
from src.plugins.ads.meta.token_store import MetaTokenStorePort, SsmTokenStore


@lru_cache(maxsize=1)
def get_token_store() -> MetaTokenStorePort:
    settings = meta_settings()
    return SsmTokenStore(settings.ssm_parameter, region=settings.region)


@lru_cache(maxsize=1)
def get_ads_port() -> MetaAdsPort:
    return GraphMetaAds()
