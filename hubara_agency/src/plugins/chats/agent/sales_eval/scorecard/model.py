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
    # Modo turno (laboratorio): `turn` = se juzga en el turno foco con el
    # prefijo como contexto; `future` = depende de turnos posteriores (cierre,
    # pedido registrado): solo pasa con la evidencia en el turno foco y si no,
    # queda `sin_senal`.
    focus: str = "turn"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    verdict: str
    turn: int | None = None
    evidence: str = ""
    critique: str = ""
    source: str = "code"
    # Cobertura por asunto (EST-08 v2): `{topic, turn, msg, covered, evidence}`.
    topics: tuple[dict[str, Any], ...] = ()


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


# `sin_senal`: solo en modo turno — el check depende de turnos posteriores al
# turno foco. Como `desconocido`, no cuenta para el cumplimiento.
VERDICTS = ("pasa", "falla", "no_aplica", "desconocido", "sin_senal")
FOCUS_MODES = ("turn", "future")


def result_dict(r: CheckResult) -> dict[str, Any]:
    row: dict[str, Any] = {
        "check_id": r.check_id,
        "verdict": r.verdict,
        "turn": r.turn,
        "evidence": r.evidence,
        "critique": r.critique,
        "source": r.source,
    }
    if r.topics:
        row["topics"] = [dict(t) for t in r.topics]
    return row
