"""Boundary DTOs del OrderReconciliationWorkflow (R-JSON).

Frozen dataclasses planos, JSON-serializables, sin tipos anidados complejos.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconcileInput:
    """Input del OrderReconciliationWorkflow.

    `vault_dir` cruza como str (R-JSON). Si llega vacío, la ACTIVITY lo
    resuelve desde `WORKSPACE_VAULT_DIR` — R-DET prohíbe que `@workflow.run`
    lea env/config, así que el default vacío + resolución en la activity es el
    patrón (idéntico al `snapshot_dir` del catalog sync).

    `max_attempts`: cap de reintentos por record antes de marcar `abandoned`.
    """

    vault_dir: str = ""
    max_attempts: int = 5


@dataclass(frozen=True)
class ReconcileResult:
    """Resumen de un barrido de reconciliación."""

    total: int
    resolved: int
    still_failing: int
    abandoned: int
    errors: int
