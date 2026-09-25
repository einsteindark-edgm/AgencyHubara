"""Contrato del puerto de percepción (DTOs frozen, JSON-serializables: R-JSON).

Las preguntas tienen la forma nativa de Jev (Decisions API de OpenRouter):

* ``noul``: sí o no; ``criteria = {"true": …, "false": …}``; responde ``p``
  (probabilidad de sí).
* ``choice``: una opción; ``criteria = {opción: descripción}``; responde
  ``choice`` con ``probs`` por opción y ``confidence``.
* ``score``: posición en una rúbrica ordenada; ``criteria = (nivel0, nivel1,
  …)``; responde ``score`` (0..n-1, puede ser fraccionario) con ``probs`` por
  nivel.

Invariante: ``ask`` NUNCA lanza. Timeout, error del proveedor, llave ausente
o respuesta con otra forma devuelven ``ok=False`` con el motivo en ``error``
y sin respuestas: el llamador sigue como si no hubiera clasificador.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

QuestionKind = Literal["noul", "choice", "score"]

# Motivos de `ok=False` (estables: van a la traza y a las métricas de caída).
ERROR_DISABLED = "disabled"
ERROR_TIMEOUT = "timeout"
ERROR_NO_API_KEY = "no_api_key"
ERROR_BAD_SHAPE = "bad_shape"
ERROR_NO_LOGPROBS = "no_logprobs"
ERROR_PROVIDER = "provider_error"


@dataclass(frozen=True)
class TypedQuestion:
    id: str
    kind: QuestionKind
    text: str
    # noul: {"true": …, "false": …} · choice: {opción: descripción} · score: niveles en orden
    criteria: Mapping[str, str] | tuple[str, ...] = ()

    @property
    def options(self) -> tuple[str, ...]:
        """Opciones válidas de un `choice` (o niveles de un `score`)."""
        return tuple(self.criteria) if not isinstance(self.criteria, Mapping) else tuple(self.criteria.keys())


@dataclass(frozen=True)
class TypedAnswer:
    id: str
    kind: QuestionKind
    p: float | None = None  # noul: P(sí)
    choice: str | None = None
    probs: tuple[tuple[str, float], ...] = ()
    confidence: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class PerceptionResult:
    ok: bool
    answers: tuple[TypedAnswer, ...] = ()
    error: str | None = None
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    notes: tuple[str, ...] = field(default=())

    def answer(self, question_id: str) -> TypedAnswer | None:
        return next((a for a in self.answers if a.id == question_id), None)


def failed(error: str, *, provider: str, model: str, latency_ms: int = 0) -> PerceptionResult:
    return PerceptionResult(ok=False, error=error, provider=provider, model=model, latency_ms=latency_ms)


class PerceptionPort(Protocol):
    """Responde preguntas tipadas sobre `state` (la ráfaga y su contexto)."""

    name: str
    model: str

    async def ask(
        self,
        state: str,
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        redact: Sequence[str] = (),
    ) -> PerceptionResult:
        """`redact`: nombres (del perfil de WhatsApp, del pedido) que el
        adaptador tapa antes de enviar cuando su perfil pide anonimizar."""
        ...
