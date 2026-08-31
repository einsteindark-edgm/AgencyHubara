"""MedusaSettings — env vars (Pydantic Settings v2).

Lee env vars con prefijo ``MEDUSA_``. Required: ``MEDUSA_BASE_URL``. Auth: o
bien ``MEDUSA_ADMIN_TOKEN`` (Opción A, recomendada), o bien
``MEDUSA_ADMIN_EMAIL`` + ``MEDUSA_ADMIN_PASSWORD`` (Opción B). El
`HttpMedusaClient` valida que al menos una pareja esté presente.

Este modulo es el unico en `platform/medusa/` que lee `os.environ`. Mismo
patron que `src/platform/config.py` para vars Temporal/WhatsApp.
"""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MedusaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEDUSA_",
        extra="ignore",
    )

    base_url: str = Field(..., description="Ej: https://medusa.hubara.example.com")

    # Premortem web-cart FM-09: docker-compose inyecta `MEDUSA_BASE_URL=""`
    # cuando la var no está exportada en el host. Vacío == ausente: debe
    # fallar en composición (degrade limpio de los consumers), no entregar
    # clientes con base_url="" que revientan por-request.
    @field_validator("base_url")
    @classmethod
    def _base_url_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MEDUSA_BASE_URL vacío — tratado como ausente")
        return value
    admin_token: str | None = Field(default=None, description="Secret API Key (Opción A)")
    # --- Store API (HU web-cart hot lead) -----------------------------------
    # Publishable API Key (Medusa Admin → Settings → Publishable API Keys).
    # Habilita `GET /store/carts/{id}` para hidratar carritos web en el
    # ingest de sales. Si está vacía, `get_web_cart_reader()` cae a
    # `NullWebCartReader` (el cart ref degrada al flujo conversacional).
    publishable_api_key: str | None = Field(
        default=None,
        description="MEDUSA_PUBLISHABLE_API_KEY — pk_... para Store API. Opcional.",
    )
    admin_email: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)
    http_timeout: float = Field(default=30.0)

    # --- Order registration (HU c4e3416f) ----------------------------------
    # Required for `MedusaOrderRegistration` (the OrderRegistrationPort
    # adapter). When unset, `get_order_registration_port()` falls back to
    # `StubOrderRegistration` (dev mode). Composition logs a warning.
    #
    # `region_id` y `sales_channel_id` se crean en Medusa Admin:
    #   Settings → Regions → <pick existing or create "Colombia / COP"> → ID
    #   Settings → Sales Channels → <pick "Default" or "WhatsApp"> → ID
    region_id: str | None = Field(
        default=None,
        description="MEDUSA_REGION_ID — id de la Region en Medusa (ej: 'reg_01...'). Required para draft orders.",
    )
    sales_channel_id: str | None = Field(
        default=None,
        description="MEDUSA_SALES_CHANNEL_ID — id del Sales Channel (ej: 'sc_01...'). Required.",
    )
    default_currency: str = Field(
        default="cop",
        description="MEDUSA_DEFAULT_CURRENCY — ISO 4217 lowercase (Medusa v2 usa 'cop', 'usd').",
    )
    default_country: str = Field(
        default="co",
        description="MEDUSA_DEFAULT_COUNTRY — ISO 3166-1 alpha-2 lowercase para shipping_address.country_code.",
    )
    # Opcional — algunas tenant configs de Medusa requieren un shipping
    # option especifico (el operador lo pre-crea en Admin → Shipping). Si esta
    # vacio, el adapter usa `list_shipping_options(region_id=...)` y elige la
    # primera. Es preferible setearlo para evitar la query extra.
    default_shipping_option_id: str | None = Field(
        default=None,
        description="MEDUSA_DEFAULT_SHIPPING_OPTION_ID — opcional. Si vacio, el adapter descubre la primera.",
    )
