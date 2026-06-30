"""Settings de la integración Meta — leídos del entorno (plugin self-contained).

Env vars (declaradas en `frontend_dashboard/src/plugins/ads/plugin.yaml`
wiring_intents.env_vars_required):
  META_APP_ID, META_APP_SECRET, META_OAUTH_REDIRECT_URI,
  META_OAUTH_SCOPES (default "ads_read"), META_ADS_TENANT (default "hubara").
El App Secret NUNCA se loguea.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MetaSettings:
    app_id: str
    app_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]
    tenant: str
    region: str | None

    @property
    def ssm_parameter(self) -> str:
        return f"/hubara/{self.tenant}/meta/oauth"

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.redirect_uri)


def meta_settings() -> MetaSettings:
    scopes = tuple(
        s.strip() for s in os.getenv("META_OAUTH_SCOPES", "ads_read").split(",") if s.strip()
    )
    return MetaSettings(
        app_id=os.getenv("META_APP_ID", ""),
        app_secret=os.getenv("META_APP_SECRET", ""),
        redirect_uri=os.getenv("META_OAUTH_REDIRECT_URI", ""),
        scopes=scopes or ("ads_read",),
        tenant=os.getenv("META_ADS_TENANT", "hubara"),
        region=os.getenv("AWS_REGION") or None,
    )
