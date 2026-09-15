"""Checks de código del scorecard (HU-SC-1).

Cada familia vive en su módulo y registra sus funciones con `@code_check`.
Una función recibe la `Trajectory` y el `CheckContext` y devuelve un
`CheckResult`. Pura: sin I/O, sin reloj.
"""
from __future__ import annotations

from collections.abc import Callable

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult

CheckFn = Callable[..., CheckResult]

CODE_CHECKS: dict[str, CheckFn] = {}


def code_check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        if check_id in CODE_CHECKS:
            raise ValueError(f"check duplicado: {check_id}")
        CODE_CHECKS[check_id] = fn
        return fn

    return register


# Registro por import: cada módulo de familia decora sus funciones.
from src.plugins.chats.agent.sales_eval.scorecard.checks import (  # noqa: E402,F401
    apertura,
    cierre,
    confirmacion,
    descubrimiento,
    envio,
    estado,
    estilo,
    ghosting,
    postcierre,
    variantes,
)
