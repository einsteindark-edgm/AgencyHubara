"""DTOs de la corrida en la caja (R-JSON)."""
from __future__ import annotations

from dataclasses import dataclass, field

LAB_TASK_QUEUE = "queue-sales-lab"  # solo existe en el Temporal de la caja (namespace hubara-lab)


@dataclass(frozen=True)
class LabRunInput:
    run_id: str


@dataclass(frozen=True)
class RunPlan:
    run_id: str
    bench_id: str
    arms: list[str]
    reps: int
    spend_limit_usd: float
    image: str = ""


@dataclass(frozen=True)
class PublishResult:
    sessions: int
    cases: int


@dataclass(frozen=True)
class ProgressUpdate:
    run_id: str
    phase: str
    turns_done: int = 0
    turns_total: int = 0
    spent_usd: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)
