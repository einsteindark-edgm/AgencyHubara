"""Cast costs → ads: the Campaigns card reads the Ads section's own contract.

``GET /api/ads/campaigns?from&to`` (``campaigns@v1``, declared in the manifest's
``consumes``) already joins Meta ad spend and the WhatsApp campaigns per
campaign. The hop goes through ``castkit`` so it carries the operator's
identity (L-16); the timeout is sized for Ads' own upstream (Meta + vault).
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request

from src.plugins.costs.providers.base import FetchContext, ProviderError
from src.sdk import castkit

_TIMEOUT_S = 22.0
_CAST_LABEL = "costs→ads"


def _provider_base() -> str:
    return os.environ.get("ADS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


def ads_cast_context(request: Request) -> FetchContext:
    async def cast_get(path: str, params: dict[str, Any]) -> Any:
        try:
            return await castkit.forward(
                request,
                "GET",
                path,
                base_url=_provider_base(),
                timeout=_TIMEOUT_S,
                cast_label=_CAST_LABEL,
                params=params,
            )
        except HTTPException as exc:
            raise ProviderError(
                f"La sección Ads respondió {exc.status_code}: {exc.detail}"
            ) from None

    return FetchContext(cast_get=cast_get)
