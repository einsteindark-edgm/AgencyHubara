"""Settings de la integración Meta — leídos del entorno (plugin self-contained).

Single-tenant (decisión 2026-07-09): el token es un system-user PROVISIONADO en
SSM `/hubara/<tenant>/meta/oauth` (no hay flujo OAuth, ver runbook en
`infra/whatsapp-provisioning/README.md`). Por eso acá solo queda lo que el
token store necesita para encontrar el parámetro:
  META_ADS_TENANT (default "hubara"), AWS_REGION (para SSM).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MetaSettings:
    tenant: str
    region: str | None

    @property
    def ssm_parameter(self) -> str:
        return f"/hubara/{self.tenant}/meta/oauth"


def meta_settings() -> MetaSettings:
    return MetaSettings(
        tenant=os.getenv("META_ADS_TENANT", "hubara"),
        region=os.getenv("AWS_REGION") or None,
    )
