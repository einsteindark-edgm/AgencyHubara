"""Paquetes de decisión (PAQUETES_DE_DECISION.md, ADR-2026-10-01).

El motor genérico que corre la "inteligencia" de una tienda escrita como
datos: modelos estrictos (`model`), condiciones CEL detrás de un puerto
(`expressions`), el certificador (`checker`) y las tablas compiladas
(`engine`). No sabe nada de ventas ni de velas: los builtins (reglas,
estado, pisos) los pone el plugin y los declara en su catálogo.
"""
from __future__ import annotations

from src.platform.decisions.checker import ENGINE_CONTRACT, check_bundle, load_bundle, load_domain
from src.platform.decisions.engine import (
    DOUBT,
    BundleError,
    CompiledBundle,
    CompiledCapability,
    Diagnostic,
    answers_from_result,
)
from src.platform.decisions.expressions import CelExpressions, ExpressionError, ExpressionPort
from src.platform.decisions.model import Bundle, BuiltinRef, Capability, Catalog, Question

__all__ = [
    "DOUBT",
    "ENGINE_CONTRACT",
    "Bundle",
    "BuiltinRef",
    "BundleError",
    "Capability",
    "Catalog",
    "CelExpressions",
    "CompiledBundle",
    "CompiledCapability",
    "Diagnostic",
    "ExpressionError",
    "ExpressionPort",
    "Question",
    "answers_from_result",
    "check_bundle",
    "load_bundle",
    "load_domain",
]
