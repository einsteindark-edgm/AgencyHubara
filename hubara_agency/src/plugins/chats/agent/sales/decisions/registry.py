"""Un solo punto decide de dónde sale cada capacidad (PAQUETES_DE_DECISION.md §10.1, F2).

Los lugares que preguntan —ingest, antes del turno, tools, egreso, después
de enviar, remarketing, abandono— nombran SOLO la decisión:

    from src.plugins.chats.agent.sales.decisions.registry import capability

    verdict = await decide(capability("baja"), inbound, …)

El resolutor la toma del **paquete activo**, que las trae todas: el catálogo
declara las que pide el código (`capabilities:`) y un paquete al que le falta
una no certifica (DB003). Nunca cae a la clase de Python (era la inteligencia
de velas para cualquier tienda): las clases quedan solo como oráculo de la
paridad (`class_capabilities`). El paquete
activo es configuración de la tienda, no código (`chats/shared/store_pack.py`:
`SALES_DECISIONS_BUNDLE`, nace en Terraform, `tenants.<t>.lab.decisions_bundle`,
default `ventas`). Cambiar de tienda o de versión de la inteligencia es ese
único punto.

Un paquete configurado que no existe (o que no compila) falla fuerte con
`BundleError`: nunca se corre la inteligencia de otra tienda por un error
de configuración. Una prueba exige que el default de Terraform exista en el
repo, y CI certifica todos los paquetes (`decisions check`).

Sin Temporal: lo usan el ingest, las tools (vía `guards`) y las activities.
"""
from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from src.plugins.chats.agent.sales.decisions.bundled import BundledCapability
from src.plugins.chats.shared import store_pack
from src.plugins.chats.shared.store_pack import BUNDLE_ENV, BUNDLES_DIR, DEFAULT_BUNDLE, active_bundle_id
from src.sdk.decisionkit import BundleError, CompiledBundle, CompiledTurn, Diagnostic, load_bundle

__all__ = [
    "BUNDLES_DIR", "BUNDLE_ENV", "DEFAULT_BUNDLE", "active_bundle", "active_bundle_id", "active_turn", "capability",
    "reset", "warm_up",
]

logger = structlog.get_logger()

_resolved: dict[tuple[str, str, str], Any] = {}
#: Un paquete que no compila se recuerda: no se recompila en cada decisión
#: (~0,5 s de CPU en el event loop de la API) y el error se registra una vez.
_broken: dict[tuple[str, str], BundleError] = {}


_CAPS = "src.plugins.chats.agent.sales.decisions.capabilities"
#: Las clases de cada capacidad: nombre → "módulo:atributo". Solo el oráculo de
#: la paridad (`class_capabilities`, pruebas): el resolutor nunca las corre.
_CLASS_PATHS: dict[str, str] = {
    "contactar": f"{_CAPS}.agente:CONTACTAR",
    "cierre": f"{_CAPS}.agente:CIERRE",
    "datos": f"{_CAPS}.datos:DATOS",
    "compra": f"{_CAPS}.lecturas:Compra",
    "retoma": f"{_CAPS}.lecturas:Retoma",
    "baja": f"{_CAPS}.lecturas:Baja",
    "acuse": f"{_CAPS}.lecturas:Acuse",
    "cortesia": f"{_CAPS}.lecturas:Cortesia",
    "cupon": f"{_CAPS}.lecturas_pedido:Cupon",
    "fuera_de_catalogo": f"{_CAPS}.lecturas_pedido:FueraDeCatalogo",
    "cantidad": f"{_CAPS}.lecturas_pedido:Cantidad",
    "categoria": f"{_CAPS}.mapeos:Categoria",
    "familia_de_color": f"{_CAPS}.mapeos:FamiliaDeColor",
    "item_del_pedido": f"{_CAPS}.mapeos:ItemDelPedido",
    "zona_de_envio": f"{_CAPS}.mapeos:ZonaDeEnvio",
    "producto_nombrado": f"{_CAPS}.mapeos:PRODUCTO_NOMBRADO",
    "persona": f"{_CAPS}.texto:PERSONA",
    "enumeracion": f"{_CAPS}.texto:ENUMERACION",
    "monto": f"{_CAPS}.texto:MONTO",
    "selector": f"{_CAPS}.texto:SELECTOR",
    "afirmacion": f"{_CAPS}.texto:AFIRMACION",
    "relevo": f"{_CAPS}.texto:RELEVO",
    "preambulo": "src.plugins.chats.agent.sales.decisions.egress:Preambulo",
    "destinatario": "src.plugins.chats.agent.sales.decisions.egress:Destinatario",
    # Variantes: la MISMA capacidad (mismo control en el panel) preguntada de otra forma.
    "destinatario_plantilla": "src.plugins.chats.agent.sales.decisions.egress:DestinatarioDePlantilla",
    "destinatario_oracion": "src.plugins.chats.agent.sales.decisions.egress:DestinatarioPorOracion",
    "rescate": "src.plugins.chats.agent.sales.decisions.egress:Rescate",
    "portavelas": "src.plugins.chats.agent.sales.decisions.egress:Portavelas",
    "saludo": "src.plugins.chats.agent.sales.decisions.egress:Saludo",
}
_instances: dict[str, Any] = {}


def _class_instance(name: str) -> Any:
    found = _instances.get(name)
    if found is None:
        module, attr = _CLASS_PATHS[name].split(":")
        value = getattr(importlib.import_module(module), attr)
        found = value() if isinstance(value, type) else value
        _instances[name] = found
    return found


def class_capabilities() -> dict[str, Any]:
    """Las capacidades que todavía tienen clase (nombre → instancia). Importa
    todas: es para las pruebas y el panel, no para el camino de un turno."""
    return {name: _class_instance(name) for name in _CLASS_PATHS}


@lru_cache(maxsize=8)
def _bundle(bundle_id: str, root: str) -> CompiledBundle:
    folder = Path(root) / bundle_id
    if not (folder / "bundle.yaml").is_file():
        known = sorted(p.parent.name for p in Path(root).glob("*/bundle.yaml"))
        raise BundleError([Diagnostic(
            "DB013", f"bundles/{bundle_id}",
            f"el paquete configurado {bundle_id!r} ({BUNDLE_ENV}) no existe; hay: {', '.join(known) or 'ninguno'}",
        )])
    return load_bundle(folder, Path(root) / "builtins.yaml")


def _load(bundle_id: str, root: str) -> CompiledBundle:
    failed = _broken.get((bundle_id, root))
    if failed is not None:
        raise failed
    try:
        return _bundle(bundle_id, root)
    except BundleError as exc:
        _broken[(bundle_id, root)] = exc
        logger.error("decisions.bundle_broken", bundle=bundle_id, error=str(exc)[:500])
        raise


def active_bundle() -> CompiledBundle:
    return _load(active_bundle_id(), str(BUNDLES_DIR))


def warm_up() -> None:
    """Compila el paquete de la tienda al arrancar (la API y los workers):
    la primera decisión no paga la compilación y el log dice qué paquete
    corre. Uno roto no tumba el arranque: queda el error (el ingest sigue
    con las reglas del código y el deploy ya lo frena antes del `up`)."""
    try:
        bundle = active_bundle()
    except BundleError:
        return  # `_load` ya lo registró
    logger.info(
        "decisions.bundle_ready", bundle=bundle.ref, capabilities=len(bundle.capabilities), turn=bundle.turn is not None
    )


def active_turn() -> CompiledTurn:
    """El turno del paquete activo (`turn.yaml`, F7): la ráfaga ①, ③ y las
    tablas de la política. `BundleError` si el paquete no lo trae."""
    bundle = active_bundle()
    if bundle.turn is None:
        raise BundleError([Diagnostic("DB015", f"bundles/{bundle.id}/turn.yaml", "el paquete activo no trae turno")])
    return bundle.turn


def capability(name: str) -> Any:
    """La capacidad `name` del paquete activo. `KeyError` si el paquete no la
    trae (nunca la clase: sería la inteligencia de otra tienda)."""
    bundle_id, root = active_bundle_id(), str(BUNDLES_DIR)
    key = (root, bundle_id, name)
    found = _resolved.get(key)
    if found is not None:
        return found
    bundle = _load(bundle_id, root)
    if name not in bundle.capabilities:
        if name in _CLASS_PATHS:
            raise KeyError(f"el paquete {bundle.ref} no trae la capacidad {name!r} (DB003: el catálogo la pide)")
        known = sorted(set(_CLASS_PATHS) | set(bundle.capabilities))
        raise KeyError(f"no existe la capacidad {name!r} (hay: {', '.join(known)})")
    found = BundledCapability(bundle.capability(name))
    _resolved[key] = found
    return found


def reset() -> None:
    """Olvida lo resuelto (pruebas, o tras cambiar la config del proceso)."""
    _resolved.clear()
    _broken.clear()
    _bundle.cache_clear()
    store_pack.reset()
