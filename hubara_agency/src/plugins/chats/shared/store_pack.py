"""El paquete de la tienda (PAQUETES_DE_DECISION.md §10.1 y F5).

Una carpeta por tienda en `decisions/bundles/<id>/`: la inteligencia del
motor de decisiones (`bundle.yaml` + `capabilities/`) y el dominio de la
tienda (`domain.yaml`: su nombre, la despedida aprobada, los ejemplos que el
agente ve en sus herramientas). Vive en `chats/shared` porque la leen los
dos agentes (ventas y remarketing), que no se importan entre sí.

El paquete activo es configuración de la tienda, no código:
`SALES_DECISIONS_BUNDLE` (nace en Terraform, `tenants.<t>.lab.decisions_bundle`),
default `ventas`. Un paquete configurado que no existe, o cuyo dominio no es
el que declara el catálogo, falla fuerte (`BundleError`): nunca se usa el
dominio de otra tienda por un error de configuración.

Sin Temporal ni I/O de red: lo leen las tools al importarse (fuera del
sandbox de los workflows), las activities y el ingest.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.sdk.decisionkit import BundleError, Diagnostic, load_domain

BUNDLES_DIR = Path(__file__).parent / "decisions" / "bundles"
#: El catálogo del motor de ventas: lo que un paquete puede pedir y el dominio que trae.
CATALOG_PATH = BUNDLES_DIR / "builtins.yaml"
#: El paquete por defecto (la tienda actual). Una tienda nueva trae el suyo y lo nombra en Terraform.
DEFAULT_BUNDLE = "ventas"
BUNDLE_ENV = "SALES_DECISIONS_BUNDLE"


def active_bundle_id() -> str:
    """El paquete de la tienda (config), o el por defecto."""
    return (os.getenv(BUNDLE_ENV) or "").strip() or DEFAULT_BUNDLE


def bundle_dir(bundle_id: str) -> Path:
    """La carpeta del paquete; `BundleError` (DB013) si no existe."""
    folder = BUNDLES_DIR / bundle_id
    if not (folder / "bundle.yaml").is_file():
        known = sorted(p.parent.name for p in BUNDLES_DIR.glob("*/bundle.yaml"))
        raise BundleError([Diagnostic(
            "DB013", f"bundles/{bundle_id}",
            f"el paquete configurado {bundle_id!r} ({BUNDLE_ENV}) no existe; hay: {', '.join(known) or 'ninguno'}",
        )])
    return folder


@lru_cache(maxsize=8)
def _domain(bundle_id: str) -> dict[str, Any]:
    return load_domain(bundle_dir(bundle_id), CATALOG_PATH)


def store_domain() -> dict[str, Any]:
    """El dominio de la tienda del paquete activo, certificado."""
    return _domain(active_bundle_id())


def vocabulary() -> dict[str, Any]:
    """Los ejemplos de la tienda que el agente ve en sus herramientas y en
    el gancho de remarketing (`domain.yaml: vocabulary`)."""
    return dict(store_domain()["vocabulary"])


def reset() -> None:
    """Olvida el dominio leído (pruebas, o tras cambiar la config del proceso)."""
    _domain.cache_clear()
