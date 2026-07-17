"""Dominio de marketing — lógica PURA (sin I/O, sin vendors, sin fastapi).

El perfil del arquetipo (P-29) recomienda que la lógica viva acá y los
routers queden delgados. I/O hacia sistemas externos: SIEMPRE vía ports del
SDK (``src.sdk.connectorkit``) desde adapters — jamás un client de vendor
directo (P-31).
"""
from __future__ import annotations


def health_payload() -> dict:
    """Ejemplo mínimo testeable sin red — reemplazar por dominio real."""
    return {"plugin": "marketing", "status": "ok"}
