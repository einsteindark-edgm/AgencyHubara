"""Adaptador falso del puerto de percepción (oficial: tests y humo del laboratorio).

Sin red. Por defecto responde de forma determinista y con la forma válida del
contrato: un `noul` es 0.9 si la palabra clave del id de la pregunta aparece en
el estado (sin tildes: `topic.catalogo` → "catalogo") y 0.1 si no; un
`choice` elige la primera opción; un `score`, el nivel 0. Un test puede fijar
respuestas por id o forzar un error.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from src.platform.perception.ports import PerceptionResult, TypedAnswer, TypedQuestion, failed


def _plain(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c)
    )


class FakePerceptionAdapter:
    name = "fake"
    model = "fake"

    def __init__(self, answers: Mapping[str, TypedAnswer] | None = None, *, error: str | None = None) -> None:
        self._answers = dict(answers or {})
        self._error = error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def _default(self, q: TypedQuestion, state: str) -> TypedAnswer:
        if q.kind == "noul":
            keyword = _plain(q.id.rsplit(".", 1)[-1].replace("_", " "))
            return TypedAnswer(id=q.id, kind="noul", p=0.9 if keyword and keyword in _plain(state) else 0.1)
        if q.kind == "choice":
            first = q.options[0]
            return TypedAnswer(id=q.id, kind="choice", choice=first, probs=((first, 1.0),), confidence=1.0)
        return TypedAnswer(id=q.id, kind="score", score=0.0, probs=(("0", 1.0),), confidence=1.0)

    async def ask(
        self,
        state: str,
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        redact: Sequence[str] = (),
    ) -> PerceptionResult:
        self.calls.append((state, tuple(q.id for q in questions)))
        if self._error:
            return failed(self._error, provider=self.name, model=self.model)
        answers = tuple(self._answers.get(q.id) or self._default(q, state) for q in questions)
        return PerceptionResult(ok=True, answers=answers, provider=self.name, model=self.model, cost_usd=0.0)
