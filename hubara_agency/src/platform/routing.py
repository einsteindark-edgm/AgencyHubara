"""Route registry declarativo de conversaciones — F6 (mata PM-2/AP-10).

Problema que resuelve: el ruteo de inbounds de WhatsApp vivía hardcodeado en
`chats` (`if active_route == ROUTE_ETA: workflow_id = f"eta-{session_id}"`),
con el MISMO template duplicado en el manifest de `orders` y la constante en
`platform/constants.py` (spinal PROTECTED). Agregar un agente conversacional
nuevo = editar un archivo central protegido + el use case de chats (violación
INV-1; hallazgo N-2b de PLUGIN_ISOLATION_AUDIT_fable.md).

Ahora cada plugin-agente DECLARA la ruta que posee en su manifest::

    agent:
      workers:
        - name: eta
          owns_route: eta
          route_workflow_id_template: "eta-{session_id}"

y este registry la resuelve genéricamente. El ruteo de inbounds (chats)
consulta el registry — **ningún agente nombra a otro**. El template vive UNA
sola vez (acá); el gate de conformance ata los `workflow_id_template` de las
transitions de otros manifests al prefijo declarado por el dueño.

Reglas:
- Las rutas CORE del runtime conversacional (`ventas`, `remarketing`,
  `humano` — `platform/constants.py`) no son registrables: pertenecen al
  núcleo, no a un plugin.
- Respeta ``ENABLED_PLUGINS``: una ruta de un plugin apagado NO resuelve →
  el caller hace su fallback (hoy: la conversación cae a Sales) en vez de
  signalear al vacío. Mismo espíritu que el dispatcher-skip (P-7).
- Colisión de rutas entre plugins = error de boot (fail-fast).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
)
from src.platform.plugin_manifest import all_manifests, enabled_plugins

_CORE_ROUTES = frozenset({ROUTE_VENTAS, ROUTE_REMARKETING, ROUTE_HUMANO})

_SESSION_TOKEN = "{session_id}"


@dataclass(frozen=True)
class ConversationRoute:
    """Dueño declarado de una `active_route` de conversación."""

    route: str
    plugin_id: str
    worker_name: str
    workflow_id_template: str  # debe contener "{session_id}"

    def workflow_id(self, session_id: str) -> str:
        return self.workflow_id_template.replace(_SESSION_TOKEN, session_id)


class RouteRegistryError(RuntimeError):
    """Manifest declara rutas inválidas (colisión, core, o template roto)."""


def _build_registry(enabled: frozenset[str] | None) -> dict[str, ConversationRoute]:
    out: dict[str, ConversationRoute] = {}
    for plugin_id, manifest in all_manifests():
        if enabled is not None and plugin_id not in enabled:
            continue
        for worker in (manifest.get("agent") or {}).get("workers") or []:
            if not isinstance(worker, dict):
                continue
            route = worker.get("owns_route")
            if not route:
                continue
            template = worker.get("route_workflow_id_template") or ""
            if route in _CORE_ROUTES:
                raise RouteRegistryError(
                    f"{plugin_id}/{worker.get('name')}: owns_route={route!r} es una "
                    f"ruta CORE del runtime conversacional — no registrable."
                )
            if _SESSION_TOKEN not in template:
                raise RouteRegistryError(
                    f"{plugin_id}/{worker.get('name')}: route_workflow_id_template="
                    f"{template!r} debe contener {_SESSION_TOKEN!r}."
                )
            if route in out:
                prev = out[route]
                raise RouteRegistryError(
                    f"ruta {route!r} declarada por {prev.plugin_id}/{prev.worker_name} "
                    f"Y {plugin_id}/{worker.get('name')} — las rutas son exclusivas."
                )
            out[route] = ConversationRoute(
                route=str(route),
                plugin_id=plugin_id,
                worker_name=str(worker.get("name")),
                workflow_id_template=template,
            )
    return out


@cache
def _cached_registry(enabled_key: frozenset[str] | None) -> dict[str, ConversationRoute]:
    return _build_registry(enabled_key)


def conversation_routes() -> dict[str, ConversationRoute]:
    """Registry `route → ConversationRoute` de los plugins HABILITADOS.

    Cacheado por (set habilitado) por proceso — los manifests y el env no
    cambian en runtime. Tests: ``conversation_routes.cache_clear()`` vía
    ``_cached_registry.cache_clear()``.
    """
    enabled = enabled_plugins()
    return _cached_registry(frozenset(enabled) if enabled is not None else None)


def resolve_route_workflow_id(route: str, session_id: str) -> str | None:
    """`workflow_id` del dueño de ``route``, o ``None`` si nadie la posee
    (ruta core, plugin apagado, o ruta desconocida) — el caller decide el
    fallback."""
    target = conversation_routes().get(route)
    if target is None:
        return None
    return target.workflow_id(session_id)
