"""DI factory para el CatalogPort.

Devuelve por default un LocalSnapshotCatalogClient apuntando al
`get_snapshot_dir()`. Singleton por proceso (lru_cache(1)) — la cache
mtime-aware vive en la instancia, asi que compartir es correcto.
"""
from __future__ import annotations

from functools import lru_cache

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir
from src.platform.catalog.port import CatalogPort


@lru_cache(maxsize=1)
def get_catalog_client() -> CatalogPort:
    return LocalSnapshotCatalogClient(
        snapshot_dir=get_snapshot_dir(),
        max_age_minutes=get_max_age_minutes(),
    )


@lru_cache(maxsize=1)
def get_checkout_verification_port():  # -> CheckoutVerificationPort
    """Verificador LIVE de precio/stock (Medusa) con el snapshot como referencia.

    Imports diferidos: `connectorkit` es lazy por símbolo y el acceso a
    `get_catalog_client` no debe arrastrar el cliente HTTP de Medusa.
    Requiere `MEDUSA_BASE_URL` (+ auth): sin config, `MedusaSettings` lanza.
    """
    from src.platform.catalog.medusa_checkout import MedusaCheckoutVerification
    from src.platform.medusa.composition import get_medusa_product_service

    return MedusaCheckoutVerification(
        medusa=get_medusa_product_service(),
        snapshot=get_catalog_client(),
    )
