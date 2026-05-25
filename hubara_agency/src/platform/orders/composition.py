"""DI factories para los ports de `platform/orders/`.

Define DOS factories:
  * `get_order_registration_port()` — escritura (write) — usado por el
    `RegisterOrderTool` para crear Draft Orders.
  * `get_order_query_port()` — lectura (read) — usado por el plugin
    `orders.api` para que el dashboard frontend liste/detalle ordenes.

Singletons por proceso (lru_cache(1)) — el `HttpMedusaClient` interno ya
es singleton via `get_medusa_client()`.

Patron: mismo shape que `src/platform/catalog/composition.py`.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from src.platform.medusa.composition import (
    get_medusa_client,
    get_medusa_product_service,
    get_medusa_settings,
)
from src.platform.orders.empty_query import EmptyOrderQuery
from src.platform.orders.medusa_order import (
    MedusaOrderConfigError,
    MedusaOrderRegistration,
)
from src.platform.orders.medusa_order_query import MedusaOrderQuery
from src.platform.orders.port import OrderRegistrationPort
from src.platform.orders.query_port import OrderQueryPort
from src.platform.orders.stub import StubOrderRegistration

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_order_registration_port() -> OrderRegistrationPort:
    """Return the configured OrderRegistrationPort (Medusa live or stub).

    - Si `MEDUSA_REGION_ID` + `MEDUSA_SALES_CHANNEL_ID` estan presentes →
      `MedusaOrderRegistration` (registra pedidos como draft_orders en Medusa).
    - Si falta cualquiera → `StubOrderRegistration` (loguea WARNING y genera
      `HUB-*` ids locales).
    """
    settings = get_medusa_settings()
    try:
        port = MedusaOrderRegistration(
            client=get_medusa_client(),
            product_service=get_medusa_product_service(),
            settings=settings,
        )
        log.info(
            "OrderRegistrationPort = MedusaOrderRegistration (region_id=%s, "
            "sales_channel_id=%s, currency=%s)",
            settings.region_id, settings.sales_channel_id, settings.default_currency,
        )
        return port
    except MedusaOrderConfigError as exc:
        log.warning(
            "OrderRegistrationPort = StubOrderRegistration (Medusa config "
            "incompleta: %s). Pedidos se registraran solo en metadata.json local. "
            "Configura MEDUSA_REGION_ID + MEDUSA_SALES_CHANNEL_ID en .env para "
            "registrar contra Medusa de verdad.",
            exc,
        )
        return StubOrderRegistration()


@lru_cache(maxsize=1)
def get_order_query_port() -> OrderQueryPort:
    """Return the configured OrderQueryPort (Medusa live or empty stub).

    - Si `MEDUSA_BASE_URL` + auth (admin_token o email+password) estan
      presentes → `MedusaOrderQuery` (consulta `/admin/orders` y
      `/admin/draft-orders`).
    - Si falta config → `EmptyOrderQuery` (devuelve listas vacias con
      `catalog_available=False` para que el frontend muestre un estado
      vacio explicito en lugar de error 500).

    NOTA: a diferencia del write port (registration), el read port NO
    requiere `region_id` ni `sales_channel_id` — solo necesita poder leer.
    Una tenant config minimal de Medusa ya devuelve sus ordenes con auth.
    """
    settings = get_medusa_settings()
    has_auth = bool(
        settings.admin_token
        or (settings.admin_email and settings.admin_password)
    )
    if not (settings.base_url and has_auth):
        log.warning(
            "OrderQueryPort = EmptyOrderQuery (Medusa no configurado). "
            "El dashboard /api/orders/orders devolvera vacio. "
            "Configura MEDUSA_BASE_URL + MEDUSA_ADMIN_TOKEN en .env."
        )
        return EmptyOrderQuery()
    try:
        port = MedusaOrderQuery(
            client=get_medusa_client(),
            settings=settings,
        )
        log.info(
            "OrderQueryPort = MedusaOrderQuery (base_url=%s)",
            settings.base_url,
        )
        return port
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "OrderQueryPort: failed to build MedusaOrderQuery (%s). "
            "Fallback a EmptyOrderQuery.",
            exc,
        )
        return EmptyOrderQuery()
