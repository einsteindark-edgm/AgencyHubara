"""MedusaSettings — env vars (Pydantic Settings v2).

Lee env vars con prefijo ``MEDUSA_``. Required: ``MEDUSA_BASE_URL``. Auth: o
bien ``MEDUSA_ADMIN_TOKEN`` (Opción A, recomendada), o bien
``MEDUSA_ADMIN_EMAIL`` + ``MEDUSA_ADMIN_PASSWORD`` (Opción B). El
`HttpMedusaClient` valida que al menos una pareja esté presente.

Este modulo es el unico en `platform/medusa/` que lee `os.environ`. Mismo
patron que `src/platform/config.py` para vars Temporal/WhatsApp.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MedusaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEDUSA_",
        extra="ignore",
    )

    base_url: str = Field(..., description="Ej: https://medusa.hubara.example.com")
    admin_token: str | None = Field(default=None, description="Secret API Key (Opción A)")
    admin_email: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)
    http_timeout: float = Field(default=30.0)
