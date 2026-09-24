"""Adaptador nulo: percepción apagada (interruptor y perfil desconocido)."""
from __future__ import annotations

from collections.abc import Sequence

from src.platform.perception.ports import ERROR_DISABLED, PerceptionResult, TypedQuestion, failed


class NullPerceptionAdapter:
    name = "null"
    model = "null"

    async def ask(
        self,
        state: str,
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        redact: Sequence[str] = (),
    ) -> PerceptionResult:
        return failed(ERROR_DISABLED, provider=self.name, model=self.model)
