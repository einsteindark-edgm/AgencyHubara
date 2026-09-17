"""Modelo del scorecard (HU-SC-1): specs, resultados y contexto de catálogo."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    family: str
    level: str
    kind: str
    applies: str
    rule: str
    origin: tuple[str, ...]
    golden_behaviors: tuple[str, ...] = ()
    twin_of: str | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    verdict: str
    turn: int | None = None
    evidence: str = ""
    critique: str = ""
    source: str = "code"


@dataclass(frozen=True)
class CheckContext:
    """Datos externos que algunos checks necesitan (catálogo vigente).

    `catalog_available=False` → los checks que dependen del catálogo devuelven
    `desconocido` en vez de adivinar.
    """

    aromas: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    product_titles: tuple[str, ...] = ()
    catalog_available: bool = False
    catalog_summary: str = ""
    # Precios unitarios (COP) del catálogo vigente — DES-10 (run ebbc203d).
    catalog_prices: tuple[int, ...] = ()


VERDICTS = ("pasa", "falla", "no_aplica", "desconocido")


def result_dict(r: CheckResult) -> dict[str, Any]:
    return {
        "check_id": r.check_id,
        "verdict": r.verdict,
        "turn": r.turn,
        "evidence": r.evidence,
        "critique": r.critique,
        "source": r.source,
    }
