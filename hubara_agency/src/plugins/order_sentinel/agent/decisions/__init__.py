"""El paquete de decisión del lector de Jev del Order Sentinel (PAQUETES_DE_DECISION.md F8).

`bundles/centinela/` trae «¿qué cambió en el pedido?» (`cambio`) y la
evidencia mensaje por mensaje (`evidencia`): preguntas, opciones, la certeza
que se pide y cómo se junta la evidencia. Es el mismo motor genérico de las
capacidades de ventas (`src.sdk.decisionkit`), certificado con
`decisions check`; lo que sigue siendo código son los builtins
(`builtins.py`, declarados en `bundles/builtins.yaml`).

Un solo paquete hoy (`centinela`): el lector de una tienda nueva sería otra
carpeta. Sin Temporal ni I/O de red: lo lee la activity del snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.plugins.order_sentinel.agent.decisions import builtins
from src.sdk.decisionkit import CompiledBundle, load_bundle

BUNDLES_DIR = Path(__file__).with_name("bundles")
CATALOG_PATH = BUNDLES_DIR / "builtins.yaml"
BUNDLE_ID = "centinela"

#: nombre → (clase, función): lo que el catálogo dice que existe.
BUILTINS: dict[str, tuple[str, Callable[..., Any]]] = {
    "no_reading": ("rule", builtins.no_reading),
    "no_evidence": ("rule", builtins.no_evidence),
    "order_conversation": ("state", builtins.order_conversation),
    "evidence_candidates": ("items", builtins.evidence_candidates),
    "jev": ("floor", builtins.jev),
    "same_value": ("same", builtins.same_value),
}


@lru_cache(maxsize=4)
def _bundle(folder: str, catalog: str) -> CompiledBundle:
    return load_bundle(Path(folder), Path(catalog))


def active_bundle() -> CompiledBundle:
    """El paquete del lector, certificado (`BundleError` si no compila)."""
    return _bundle(str(BUNDLES_DIR / BUNDLE_ID), str(CATALOG_PATH))


def call(ref: Any, *args: Any, **kwargs: Any) -> Any:
    """Corre el builtin que pide el paquete (`state:`, `items:`…) con sus `with:`."""
    _kind, fn = BUILTINS[ref.builtin]
    return fn(*args, **{**dict(ref.params), **kwargs})


def reset() -> None:
    """Olvida el paquete leído (pruebas)."""
    _bundle.cache_clear()


__all__ = ["BUILTINS", "BUNDLES_DIR", "BUNDLE_ID", "CATALOG_PATH", "active_bundle", "call", "reset"]
