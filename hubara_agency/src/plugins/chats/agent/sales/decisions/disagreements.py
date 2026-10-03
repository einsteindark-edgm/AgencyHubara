"""La cola de desacuerdos del motor vive en la plataforma (una sola para todo
el sistema: ventas y el Order Sentinel). Este nombre queda para los que ya la
importaban desde el motor."""
from __future__ import annotations

from src.sdk.connectorkit import DisagreementLog

__all__ = ["DisagreementLog"]
