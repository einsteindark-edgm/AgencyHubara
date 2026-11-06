"""Composition root of the Costs plugin (process-wide state lives here, R-STATELESS)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache

from src.plugins.costs.providers.fx import TrmSource
from src.plugins.costs.service import CostsService
from src.plugins.costs.settings import CostsSettings, build_providers, load_config


@lru_cache(maxsize=1)
def get_costs_service() -> CostsService:
    settings = CostsSettings.from_env(os.environ, load_config())
    return CostsService(
        build_providers(settings),
        TrmSource(
            fallback_cop_per_usd=settings.fx_fallback_cop_per_usd,
            per_usd=settings.fx_per_usd,
        ),
        timeout_s=settings.timeout_s,
        live_ttl_s=settings.live_ttl_s,
        closed_ttl_s=settings.closed_ttl_s,
        now=lambda: datetime.now(timezone.utc),
    )
