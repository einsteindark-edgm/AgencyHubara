"""Protocolos estructurales del plugin system (F8 — la respuesta a PM-3).

Idea (protocol-oriented, à la Swift/Apple): un plugin no "promete" cosas en
un doc — **conforma protocolos chequeables**. Cada superficie que un manifest
declara tiene un protocolo estructural que el código DEBE satisfacer, y cada
protocolo tiene su verificador en una de tres capas:

1. **Compilación / análisis estático** (gratis, lo más temprano):
   - TS: el registry generado fuerza ``PluginModule`` (default export es un
     componente) vía ``assertPluginModule`` — un entry roto = error de tsc.
   - Python: estos ``typing.Protocol`` permiten anotar/checkear puntos de
     integración sin acoplar por herencia (structural typing — el plugin no
     importa NADA para conformar).

2. **Boot / runtime fail-fast**:
   - ``main.py`` exige ``router: APIRouter`` en el módulo api declarado.
   - ``run_workers.py`` exige ``async def main()`` en el módulo worker.
   - ``plugin_loader.validate_enabled`` (P-6) + ``plugin_runtime.
     ensure_plugin_enabled`` (P-21) + ``routing`` (rutas declaradas).

3. **CI (tests/architecture)** — el conformance gate:
   ``test_plugin_conformance.py`` + ``test_manifest_orchestration_
   consistency.py`` + ``test_plugin_contract.py`` verifican por AST/
   filesystem que TODO lo declarado existe (workflows, workspaces, queues,
   agentic, rutas, casts...). **Regla de oro: ningún campo nuevo del manifest
   sin su check** — un campo sin verificador es una mentira en potencia.

Estos Protocols son la referencia tipada de la capa 2. Son ``runtime_checkable``
para poder usarlos en asserts de boot si un loader nuevo los necesita.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter


@runtime_checkable
class ApiModule(Protocol):
    """Lo que ``api.python_module`` / ``legacy_routers[].module`` debe exponer.

    Verificado al boot por ``src.main._register_router_from_module`` (un módulo
    sin ``router`` o con un ``router`` que no es ``APIRouter`` mata el boot).
    """

    router: APIRouter


@runtime_checkable
class WorkerModule(Protocol):
    """Lo que ``agent.workers[].module`` debe exponer.

    Verificado al boot por ``src.run_workers._run_worker`` (sin ``async def
    main()`` el launcher falla con mensaje accionable) y, en los containers de
    producción (que ejecutan ``python -m <module>`` directo), por el self-gate
    P-21 (``plugin_runtime.ensure_plugin_enabled``) que el conformance gate
    exige como primera línea del ``main()``.
    """

    async def main(self) -> None:  # pragma: no cover — firma estructural
        ...


@runtime_checkable
class ConversationRouteOwner(Protocol):
    """Forma del worker-spec que posee una ruta de conversación (F6).

    No es un módulo: es la ENTRADA del manifest (``agent.workers[]`` con
    ``owns_route`` + ``route_workflow_id_template``). La valida
    ``platform.routing._build_registry`` al construir el registry (colisiones,
    rutas core, template sin ``{session_id}`` → ``RouteRegistryError``).
    """

    def get(self, key: str, default: Any = None) -> Any:  # pragma: no cover
        ...
