"""TenantKit — de qué tienda es cada cosa (Acktos Stores).

Acktos Stores es una plataforma donde cada tienda es configuración. La entrada
(webhook, API, Temporal, ciclos) fija la tienda con ``tenant_scope``; todo lo
demás la lee con ``current_tenant()``, sin pasarla de mano en mano.

- En la caja de Hubara, fuera de un alcance, ``current_tenant()`` es
  ``"hubara"``: corre el mismo motor como única tienda.
- En una caja de una sola tienda, la que nombra ``ACKTOS_TENANT``.
- En la celda (``ACKTOS_CELL=1``) no hay tienda por defecto: fuera de un
  alcance lanza ``TenantNotInScope``, nunca responde otra tienda.

Uso canónico::

    from src.sdk.tenantkit import current_tenant, tenant_scope

    with tenant_scope("vincenzo"):
        current_tenant()                 # "vincenzo"

Ningún plugin lee ``ACKTOS_TENANT`` ni ``ACKTOS_CELL`` del entorno (guard:
``tests/platform/test_tenantkit.py``). Doc: ``docs/_sdk/19-tenantkit.md``.
"""
from __future__ import annotations

# Alias idiom (regla 1 SDK): sin el `as x`, ruff --fix poda el re-export.
from src.platform.tenant import (
    InvalidTenantId as InvalidTenantId,
    TenantNotInScope as TenantNotInScope,
    current_tenant as current_tenant,
    tenant_scope as tenant_scope,
    validate_tenant_id as validate_tenant_id,
)
