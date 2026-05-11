"""platform.medusa — adapter HTTP a Medusa Admin API v2.

Cross-agent infrastructure: este paquete expone `HttpMedusaClient` y
`MedusaProductService` para que cualquier activity (de hoy o futura) los
consuma vía `composition.get_medusa_client()`.

R-DIP: este paquete NO importa de ningún agente. Sus consumers son
`src/catalog_sync/...` (HU-03) y, opcionalmente en futuro, tools del Sales
agent que necesiten datos en vivo (stock real-time, pricing por región).
"""
from src.platform.medusa.client import (
    DEFAULT_PRODUCT_FIELDS,
    HttpMedusaClient,
    MedusaAPIError,
)
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings

__all__ = [
    "DEFAULT_PRODUCT_FIELDS",
    "HttpMedusaClient",
    "MedusaAPIError",
    "MedusaProductService",
    "MedusaSettings",
]
