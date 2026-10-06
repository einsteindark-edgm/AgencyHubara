"""El paquete de decisión de la App Operador (`operador`).

`bundles/operador/` trae «¿qué burbuja va primero?» (`burbuja`) y «¿qué tan
grave es este incendio, de qué tipo y si empeora?» (`incendio`): preguntas,
opciones, la certeza que se pide y cómo se arma el valor. Es el motor genérico
de las capacidades de ventas (`src.sdk.decisionkit`), con su propio catálogo
(`bundles/builtins.yaml`) y certificado con `decisions check`. Lo que sigue
siendo código son los builtins (`builtins.py`): el texto que ve Jev, las
opciones (las jugadas legales de las reglas), las reglas de respaldo y el piso.

Sin Temporal ni I/O de red. Corre por el motor oficial: la API móvil
(`chats/api/mobile_decisions.py`) envuelve cada capacidad en `BundledCapability`
con `builtin` de acá y la decide con `decide_for_session` (el modo de cada
conversación sale del panel «Motor de decisiones»).
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.plugins.chats.shared.operator.decisions import builtins
from src.sdk.decisionkit import CompiledBundle, load_bundle

BUNDLES_DIR = Path(__file__).with_name("bundles")
CATALOG_PATH = BUNDLES_DIR / "builtins.yaml"
BUNDLE_ID = "operador"

#: nombre → (clase, función): lo que el catálogo dice que existe.
BUILTINS: dict[str, tuple[str, Callable[..., Any]]] = {
    "rules_first": ("rule", builtins.rules_first),
    "chat_board": ("state", builtins.chat_board),
    "legal_bubbles": ("options", builtins.legal_bubbles),
    "rules_fire": ("rule", builtins.rules_fire),
    "fire_board": ("state", builtins.fire_board),
    "jev": ("floor", builtins.jev),
    "safety_stays_grave": ("floor", builtins.safety_stays_grave),
    "same_value": ("same", builtins.same_value),
}


@lru_cache(maxsize=4)
def _bundle(folder: str, catalog: str) -> CompiledBundle:
    return load_bundle(Path(folder), Path(catalog))


def active_bundle() -> CompiledBundle:
    """El paquete de la app, certificado (`BundleError` si no compila)."""
    return _bundle(str(BUNDLES_DIR / BUNDLE_ID), str(CATALOG_PATH))


def builtin(kind: str, name: str) -> Callable[..., Any]:
    """La implementación del builtin `name` de clase `kind` (lo que pide
    `BundledCapability` para correr este paquete por el motor oficial)."""
    found = BUILTINS.get(name)
    if found is None or found[0] != kind:
        raise KeyError(f"builtin {kind} desconocido en el paquete {BUNDLE_ID}: {name!r}")
    return found[1]


def reset() -> None:
    """Olvida el paquete leído (pruebas)."""
    _bundle.cache_clear()


__all__ = ["BUILTINS", "BUNDLES_DIR", "BUNDLE_ID", "CATALOG_PATH", "active_bundle", "builtin", "reset"]
