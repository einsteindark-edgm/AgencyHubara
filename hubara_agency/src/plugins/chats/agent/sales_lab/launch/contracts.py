"""DTOs del lanzador de corridas (R-JSON: frozen y JSON-serializables)."""
from __future__ import annotations

from dataclasses import dataclass, field

TERMINAL_PHASES = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True)
class LabLaunchInput:
    run_id: str
    arms: list[str]
    reps: int
    bench_id: str | None  # None = exportar un banco nuevo
    image: str
    since_ms: int
    estimate_usd: float
    # La caja corta la corrida si el gasto real llega acá: el menor entre el
    # tope por corrida y lo que queda del tope del mes (plan §3.7).
    spend_limit_usd: float = 0.0


@dataclass(frozen=True)
class ExportBenchInput:
    bench_id: str
    since_ms: int


@dataclass(frozen=True)
class BenchInfo:
    bench_id: str
    sessions: int
    customer_turns: int
    files: int


@dataclass(frozen=True)
class LabOrder:
    """`orders/<run_id>.json`: lo que la caja necesita y la orden por SSM no lleva."""

    run_id: str
    bench_id: str
    arms: list[str]
    reps: int
    image: str
    estimate_usd: float
    spend_limit_usd: float
    requested_at_ms: int


@dataclass(frozen=True)
class DispatchInput:
    run_id: str
    image: str


@dataclass(frozen=True)
class PollInput:
    run_id: str
    poll_s: float = 10.0
    start_grace_s: float = 900.0  # la caja baja la imagen y el banco antes de reportar
    stale_after_s: float = 1200.0  # sin reportar 20 min = la caja se cayó


@dataclass(frozen=True)
class LabProgress:
    """`runs/<run_id>/progress.json` (lo escribe la caja)."""

    run_id: str
    phase: str  # preparing | running | evaluating | done | failed | cancelled
    turns_done: int = 0
    turns_total: int = 0
    spent_usd: float = 0.0
    error: str | None = None
    started_at_ms: int | None = None
    updated_at_ms: int | None = None
    notes: list[str] = field(default_factory=list)
