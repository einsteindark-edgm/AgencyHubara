"""Tipos del plan del turno y la regla de la capa ②. PURO y seguro dentro del
workflow: no importa el puerto ni hace I/O.

Las políticas (`policies/`) arman el plan con las respuestas de Jev; el
workflow aplica las reglas que la activity grabó (`TurnDecisions.coverage`)
con `uncovered_topics`, que es la única lógica de ② que corre en el workflow.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlanTopic:
    topic: str
    msg: int | None = None
    p: float | None = None


@dataclass(frozen=True)
class TurnPlan:
    ok: bool
    topics: tuple[PlanTopic, ...] = ()
    stage: str | None = None
    error: str | None = None

    def labels(self) -> list[str]:
        return [t.topic for t in self.topics]


@dataclass(frozen=True)
class TurnOutcome:
    """Lo que una política decide antes del turno (`decide_turn`): el plan, los
    asuntos con su etiqueta, la nota para el LLM, las reglas de la capa ② y la
    lectura del hilo (qué preguntó el asesor, qué responde el cliente)."""

    plan: TurnPlan
    topics: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    reading: dict[str, Any] = field(default_factory=dict)
    # F6: tools requeridas (contrato asunto → tool) y guía de la etapa.
    tools: dict[str, Any] = field(default_factory=dict)
    guide: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageDecision:
    decision: str  # send | complement | pending
    missing: tuple[str, ...] = ()
    covered: dict[str, float] = field(default_factory=dict)


def plain(text: str) -> str:
    """Minúsculas y sin tildes (para comparar palabras)."""
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


def answer_of(result: Any, qid: str) -> Any:
    return next((a for a in getattr(result, "answers", ()) or () if getattr(a, "id", None) == qid), None)


def uncovered_topics(
    topics: Sequence[PlanTopic],
    rules: Mapping[str, Mapping[str, Any]],
    *,
    tools_used: Iterable[str],
    shown: str,
) -> list[PlanTopic]:
    """② Asuntos que nada de lo que el CLIENTE VE en el turno atiende.

    `shown` es el texto que de verdad le llega (lo que validó `send_reply` y
    los textos de las tools que salieron), nunca la narración que el
    default-deny descarta. Cada regla: `tools` que lo atienden, `words` del
    texto (sin tildes) y `any_text` (cualquier texto lo atiende, p. ej. un
    aplazamiento). Un asunto sin regla no se juzga (no pide ronda)."""
    used = set(tools_used)
    text = plain(shown)
    missing: list[PlanTopic] = []
    for t in topics:
        rule = rules.get(t.topic)
        if not isinstance(rule, Mapping):
            continue
        by_tool = bool(used & set(rule.get("tools") or ()))
        by_words = bool(text) and any(w and w in text for w in rule.get("words") or ())
        by_any = bool(rule.get("any_text")) and bool(text.strip())
        if not (by_tool or by_words or by_any):
            missing.append(t)
    return missing
