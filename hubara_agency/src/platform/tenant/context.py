"""De qué tienda es cada cosa: el contexto de tienda (Acktos Stores, F1.1).

Toda entrada (webhook, API, Temporal, ciclos) fija la tienda con
``tenant_scope(tienda)``; todo lo demás la lee con ``current_tenant()``. Es una
``ContextVar``: cada tarea de asyncio ve la suya, y al salir del alcance vuelve
la de antes aunque el bloque falle.

Fuera de todo alcance:

- **La caja de Hubara** responde ``hubara``, sin tocar su entorno: corre el
  mismo motor como única tienda (la tienda por defecto).
- **Una caja de una sola tienda** la nombra con ``ACKTOS_TENANT``.
- **La celda** (``ACKTOS_CELL=1``) atiende varias tiendas y no tiene tienda
  por defecto: leerla fuera de un alcance lanza ``TenantNotInScope``. Nunca
  responde otra tienda.

Solo usa la stdlib: lo importan el webhook, las activities y (en F1.2) los
interceptores de Temporal.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# La tienda de la caja de siempre: corre el mismo motor como única tienda.
DEFAULT_STORE = "hubara"
# Una caja de una sola tienda la nombra acá.
TENANT_ENV = "ACKTOS_TENANT"
# La celda atiende varias tiendas: no tiene tienda por defecto.
CELL_ENV = "ACKTOS_CELL"

# La tienda nombra su carpeta del vault, el prefijo de sus IDs de Temporal, su
# árbol de SSM, su base de Medusa y su grupo de Cognito: el slug de forge.
_TENANT_RE = re.compile(r"[a-z][a-z0-9]{1,19}")
_RESERVED = frozenset({"graphagents", "lab"})
_RESERVED_PREFIXES = ("aws", "ssm", "amazon")

_SCOPED: ContextVar[str | None] = ContextVar("acktos_tenant", default=None)


class TenantNotInScope(RuntimeError):
    """La celda no tiene tienda por defecto y este código corre fuera de un alcance."""


class InvalidTenantId(ValueError):
    """El id no sirve para nombrar los recursos de una tienda."""


def current_tenant() -> str:
    """La tienda del alcance actual; fuera de uno, la de la caja (nunca en la celda)."""
    scoped = _SCOPED.get()
    if scoped is not None:
        return scoped
    if _is_cell():
        raise TenantNotInScope(
            "la celda atiende varias tiendas y no tiene una por defecto: falta "
            "fijarla con tenant_scope() en la entrada (webhook, API, Temporal o ciclo)"
        )
    return validate_tenant_id(os.environ.get(TENANT_ENV) or DEFAULT_STORE)


@contextmanager
def tenant_scope(tenant: str) -> Iterator[str]:
    """Todo lo que corre dentro del bloque es de ``tenant`` (validada antes de entrar)."""
    token = _SCOPED.set(validate_tenant_id(tenant))
    try:
        yield tenant
    finally:
        _SCOPED.reset(token)


def validate_tenant_id(tenant: str) -> str:
    """``tenant`` si puede nombrar los recursos de una tienda; si no, ``InvalidTenantId``.

    Es el piso contra un id que llega de afuera (ruta del webhook, claim del
    login, encabezado de Temporal) y termina en una ruta del vault o en un ID.
    """
    if (
        not isinstance(tenant, str)
        or not _TENANT_RE.fullmatch(tenant)
        or tenant in _RESERVED
        or tenant.startswith(_RESERVED_PREFIXES)
    ):
        raise InvalidTenantId(
            f"tienda {tenant!r}: de 2 a 20 minúsculas o dígitos, empieza con una letra "
            "(sin «_» ni «-»); no puede ser graphagents ni lab, ni empezar por aws, ssm o amazon"
        )
    return tenant


def _is_cell() -> bool:
    return (os.environ.get(CELL_ENV) or "").strip().lower() in ("1", "true", "yes", "on")
