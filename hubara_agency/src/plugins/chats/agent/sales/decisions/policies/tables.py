"""Las tablas de una política del turno (PAQUETES_DE_DECISION.md F7).

Una política (`turno-v1`…`v3`) arma el turno con tablas: qué atiende cada
asunto (②), las notas de la lectura del hilo, el contrato asunto → tools, la
banda de ③ y la guía de etapas. Con un perfil del paquete (`jev-v5`) las
tablas salen del `turn.yaml` del paquete activo (`decisions/turn.py` las
arma con esta forma); sin ellas (`tables=None`, perfiles jev-v1…v4), la
política usa sus constantes de siempre. PURO.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: ② de un asunto: (tools que lo atienden, palabras del texto, cualquier texto).
CoverageRule = tuple[frozenset[str], tuple[str, ...], bool]


@dataclass(frozen=True)
class TurnTables:
    coverage: Mapping[str, CoverageRule]
    #: (lo que preguntó el asesor, lo que responde el cliente) → la nota.
    reading_notes: Mapping[tuple[str, str], str]
    #: lo que preguntó el asesor → la nota, responda lo que responda.
    reading_any: Mapping[str, str]
    choice_topics: tuple[str, ...]
    no_sale_stages: tuple[str, ...]
    stagnant_turns: int
    slot_labels: Mapping[str, str]
    #: El contrato: (asuntos del plan, respuestas de Jev, etapa) → lo que pide
    #: cada asunto (`{topic, any_of, nudge}`), en el orden de los asuntos.
    contract: Callable[[Sequence[str], Any, str | None], list[dict[str, Any]]]
    #: ③: (plan, respuestas de Jev) → `CoverageDecision`.
    verify: Callable[[Any, Any], Any]
