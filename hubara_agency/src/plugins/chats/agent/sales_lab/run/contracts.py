"""DTOs de la corrida en la caja (R-JSON)."""
from __future__ import annotations

from dataclasses import dataclass, field

LAB_TASK_QUEUE = "queue-sales-lab"  # solo existe en el Temporal de la caja (namespace hubara-lab)


@dataclass(frozen=True)
class LabRunInput:
    run_id: str
    # Eventos de historia a partir de los cuales la corrida deja de simular y
    # califica lo que alcanzó: Temporal corta un workflow en 51.200 eventos.
    history_limit: int = 40_000


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


@dataclass(frozen=True)
class SmokeResult:
    """El turno de humo: un caso real del banco, de punta a punta en el sandbox."""

    ok: bool
    case_id: str | None = None
    error: str | None = None
    sent_texts: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class SimulateInput:
    """Un caso (línea `index` de cases.jsonl) de un brazo y una repetición."""

    run_id: str
    bench_id: str
    arm: str
    rep: int
    index: int


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    ok: bool
    error: str | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ArmPublishInput:
    run_id: str
    arm: str
    rep: int


@dataclass(frozen=True)
class ArmPublishResult:
    published: int
    missing: int


@dataclass(frozen=True)
class EvaluateInput:
    """Calificar un pedazo de un brazo en una repetición (modo turno, PR 13):
    las sesiones que caben en el pedazo desde la `offset` (en orden)."""

    run_id: str
    bench_id: str
    arm: str
    rep: int
    judge: bool = True
    offset: int = 0


@dataclass(frozen=True)
class EvaluateResult:
    episodes: int
    judge_errors: int = 0
    turns: int = 0  # turnos calificados en el pedazo
    judge_usd: float = 0.0  # lo que gastó el juez en el pedazo
    next_offset: int | None = None  # None = el brazo quedó calificado


@dataclass(frozen=True)
class SummarizeInput:
    run_id: str
    arms: list[str]
    reps: int
    # Repeticiones calificadas por brazo (el tope o el límite de la corrida
    # pueden cortar antes de `reps`) y los bots que no alcanzaron a simularse.
    reps_by_arm: dict[str, int] = field(default_factory=dict)
    arms_pending: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SummarizeResult:
    notes: list[str] = field(default_factory=list)
