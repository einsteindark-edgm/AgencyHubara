"""Ports del ConnectorKit + sus vendors. El port es el contrato; el vendor la
implementación intercambiable. Para los golden-replay se usa el vendor `fixture`
(determinista, sin red) — así G-DET se sostiene.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class InsightsPort(Protocol):
    """Insights de campañas (gasto, impresiones, clicks, conversiones, …)."""

    def fetch(self, *, account_id: str, since: str, until: str) -> list[dict]: ...


class FixtureMetaInsights:
    """Vendor de test/golden: devuelve filas precargadas en vez de pegarle a Meta."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetch(self, *, account_id: str, since: str, until: str) -> list[dict]:
        return list(self._rows)


# TODO (G2):
#   LiveMetaInsights      — Meta Marketing API (timeout dimensionado por la cadena
#                           real de Meta, no por el hop local; retries con backoff).
#   WarehouseMetaInsights — lee de un warehouse ya ingestado.

# Registry declarativo: nombre de port → contrato. Lo lee el `consumes:` del manifest.
PORTS: dict[str, str] = {
    "meta_marketing_api": "InsightsPort",
    # "ctwa_vault": "...",  # G3
}
