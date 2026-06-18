"""DTOs frozen del harness de evaluación (R-JSON).

Cruzan el boundary workflow↔activity, así que son `@dataclass(frozen=True)` con
**solo campos escalares** (str/int/float/bool). Deliberadamente NO llevan
colecciones a través del boundary: el detalle por-métrica de cada conversación
se emite a SigNoz DENTRO de la activity (span attrs + métrica), y el workflow
solo agrega contadores escalares. Esto evita sorpresas de (de)serialización de
colecciones anidadas en el data converter de Temporal.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalWindowInput:
    """Parámetros de una corrida de evaluación online (input del workflow).

    Inmutable y replay-safe: el workflow lo recibe del Schedule y lo pasa tal
    cual a las activities.
    """

    lookback_hours: int = 8
    """Ventana hacia atrás desde ahora: qué conversaciones considerar."""

    max_conversations: int = 50
    """Tope de conversaciones a evaluar por corrida (presupuesto de juez)."""

    min_turns: int = 4
    """Saltear conversaciones triviales (< N turnos): no hay qué evaluar."""

    candidate_threshold: float = 0.7
    """Si el score promedio de una conversación < esto → candidata a golden."""

    draft_goldens: bool = True
    """Si True, el juez redacta la respuesta corregida para los candidatos
    (auto-curación). Si False, solo se marca el candidato sin draft."""

    redact_pii: bool = True
    """Redactar teléfonos/emails antes de mandar al juez y de escribir candidatos."""

    chunk_size: int = 5
    """Concurrencia de fan-out: cuántas conversaciones evaluar en paralelo por
    chunk (acota la carga sobre el LLM-juez). El workflow itera por chunks."""


@dataclass(frozen=True)
class ConversationEvalResult:
    """Resultado de evaluar UNA conversación (output de la activity).

    Solo escalares — el detalle por-métrica (score+reason por cada métrica) ya
    se emitió a SigNoz dentro de la activity (span attrs + histograma).
    """

    session_id: str
    episode_id: str = ""
    whatsapp_number: str = ""
    num_turns: int = 0
    metrics_evaluated: int = 0
    metrics_passed: int = 0
    avg_score: float = 0.0
    overall_pass: bool = True
    is_candidate: bool = False
    candidate_path: str = ""
    skipped: bool = False
    error: str = ""


@dataclass(frozen=True)
class EvalRunSummary:
    """Agregado de una corrida completa (output del workflow)."""

    window_hours: int = 0
    selected: int = 0
    evaluated: int = 0
    passed: int = 0
    failed: int = 0
    candidates: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class GoldenEvalInput:
    """Parámetros de una corrida del GOLDEN set (input de GoldenEvalWorkflow).

    A diferencia de EvalWindowInput (muestreo online de conversaciones reales),
    esto corre el MISMO set de escenarios controlado que el GitHub Action — el
    agente REAL es manejado por cada escenario y los scores se emiten a SigNoz con
    `eval.suite=golden`. Inmutable y replay-safe.
    """

    scenario: str = ""
    """id(s) de escenario separados por coma (vacío = los 61)."""

    category: str = ""
    """Filtrar por categoría (vacío = todas)."""

    repeat: int = 1
    """Correr cada escenario N veces (trending: agente+juez no-deterministas)."""

    no_judge: bool = False
    """Solo behaviors deterministas (sin juez LLM) — rápido y barato."""


@dataclass(frozen=True)
class GoldenSuiteResult:
    """Agregado de una corrida del golden set (output de GoldenEvalWorkflow)."""

    scenarios: int = 0
    behaviors_ok: int = 0
    """Escenarios donde TODOS los behaviors deterministas pasan."""
    errored: int = 0
    judge: bool = False
    """Si corrió el juez (y por ende emitió scores a SigNoz)."""
    duration_s: int = 0


@dataclass(frozen=True)
class EvaluateEpisodeInput:
    """Input de `EvaluateEpisodeWorkflow` — evaluación dirigida de UN episodio.

    Lo arma el dispatcher desde `EpisodeClosedEvent` (transition
    `sales_episode_close_triggers_eval`): cuando un episodio cierra, se evalúa
    SOLO ese episodio (no un muestreo). `closing_tag` viaja para observability
    (qué cierre disparó la eval); el workflow construye el unit_id
    `<session>::<episode>` y delega en la activity de eval.

    Inmutable y replay-safe (R-JSON): solo escalares.

    `min_turns` / `candidate_threshold` (ACK-4): umbrales de la eval, antes
    hardcoded en el workflow. Viajan como input (defaults 4 / 0.7) para que un
    caller los tune (backfill, tests). El override operativo por env lo aplica
    la eval activity (R-DET: el workflow no lee env).
    """

    session_id: str
    episode_id: str = ""
    closing_tag: str = ""
    min_turns: int = 4
    candidate_threshold: float = 0.7
