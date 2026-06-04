"""Métricas de evaluación del Asesor de Ventas.

Dos familias complementarias:

  * **Deterministas** (sin LLM, baratas, NO flaky) — checks mecánicos del guion
    como custom `BaseConversationalMetric`: el saludo de apertura y el estilo
    (cero voseo, cero em dash, emoji allowlist, sin frases de cierre prohibidas).
    Son guardas duras: una violación es una violación, sin ambigüedad.

  * **LLM-juez** (`ConversationalGEval` + built-ins) — el juicio matizado:
    adherencia al guion (la pieza central), oferta proactiva, no-alucinación,
    avance de conversión, handoff correcto, role adherence, knowledge retention.

`deepeval` se importa **lazy**: el módulo es importable sin el extra `evals`
(gate de arquitectura). El base de las métricas deterministas se importa con
guard (`object` si deepeval ausente) para que la definición de clase no rompa.
"""
from __future__ import annotations

import re
from typing import Any

from src.plugins.chats.agent.sales_eval.evals import script_rubric as R

try:  # guard: importable sin el extra `evals` (gate de arquitectura)
    from deepeval.metrics import BaseConversationalMetric as _BaseConvMetric
except Exception:  # noqa: BLE001
    _BaseConvMetric = object  # type: ignore[assignment,misc]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def metric_key(metric: Any) -> str:
    """Clave estable snake_case de una métrica (para `metric.name` en SigNoz)."""
    key = getattr(metric, "key", None)
    if key:
        return str(key)
    name = getattr(metric, "name", None) or getattr(metric, "__name__", "metric")
    return _SLUG_RE.sub("_", str(name).strip().lower()).strip("_")


# ---------------------------------------------------------------------------
# Helpers sobre el test case (turns).
# ---------------------------------------------------------------------------

def _assistant_turns(test_case: Any) -> list[Any]:
    return [t for t in (test_case.turns or []) if getattr(t, "role", None) == "assistant"]


def _first_assistant(test_case: Any) -> Any | None:
    for t in (test_case.turns or []):
        if getattr(t, "role", None) == "assistant":
            return t
    return None


# ---------------------------------------------------------------------------
# Métricas DETERMINISTAS.
# ---------------------------------------------------------------------------

class GreetingComplianceMetric(_BaseConvMetric):  # type: ignore[misc,valid-type]
    """¿La apertura saludó por hora + marca y NO usó una apertura prohibida?

    Determinista. Mira el PRIMER turno del asistente (génesis de la sesión).
    """

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.key = "greeting_compliance"
        self.async_mode = False
        self.score = None
        self.reason = None
        self.success = None
        self.error = None
        self.evaluation_cost = 0.0

    @property
    def __name__(self) -> str:  # noqa: D401
        return "Greeting Compliance"

    def measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        first = _first_assistant(test_case)
        if first is None:
            self.score, self.success = 1.0, True
            self.reason = "Sin turnos del asistente — saludo no aplica."
            return self.score
        content = getattr(first, "content", "") or ""
        bad_opener = next((p.pattern for p in R.FORBIDDEN_OPENERS if p.search(content)), None)
        greeting_ok = bool(R.GREETING_RE.search(content) and R.BRAND_RE.search(content))
        if bad_opener:
            self.score, self.success = 0.0, False
            self.reason = f"Apertura prohibida en el primer mensaje (patrón {bad_opener!r})."
        elif greeting_ok:
            self.score, self.success = 1.0, True
            self.reason = "Apertura correcta: saludo por hora + marca Hubara."
        else:
            self.score, self.success = 0.0, False
            self.reason = (
                "El primer mensaje no abre con saludo por hora de Colombia "
                "(Buenos días/tardes/noches) + marca 'Hubara'."
            )
        return self.score

    async def a_measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)


class StyleComplianceMetric(_BaseConvMetric):  # type: ignore[misc,valid-type]
    """Estilo del guion: cero voseo, cero em dash, emoji allowlist, sin cierres prohibidos.

    Determinista. Score graduado: 1 − 0.25·(#dimensiones violadas) sobre 4
    dimensiones. Umbral 1.0 por defecto (cualquier violación de estilo falla).
    """

    _DIMENSIONS = ("voseo", "em_dash", "emoji", "cierre_prohibido")

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.key = "style_compliance"
        self.async_mode = False
        self.score = None
        self.reason = None
        self.success = None
        self.error = None
        self.evaluation_cost = 0.0

    @property
    def __name__(self) -> str:  # noqa: D401
        return "Style Compliance"

    def measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        violations: dict[str, str] = {}
        for turn in _assistant_turns(test_case):
            content = getattr(turn, "content", "") or ""
            if "voseo" not in violations:
                hit = next(
                    (p.pattern for p in (R.VOSEO_RES + R.EFFUSIVE_RES) if p.search(content)),
                    None,
                )
                if hit:
                    violations["voseo"] = f"voseo/efusividad: {hit!r}"
            if "em_dash" not in violations and R.DASH_RE.search(content):
                violations["em_dash"] = "em dash (—/–) en respuesta al cliente"
            if "emoji" not in violations:
                bad = R.disallowed_emojis(content)
                over = any(
                    len(R.find_emojis(b)) > 1 for b in content.split("\n\n")
                )
                if bad:
                    violations["emoji"] = f"emoji fuera de allowlist: {bad}"
                elif over:
                    violations["emoji"] = "más de 1 emoji en una burbuja"
            if "cierre_prohibido" not in violations:
                hit = next((p.pattern for p in R.FORBIDDEN_CLOSINGS if p.search(content)), None)
                if hit:
                    violations["cierre_prohibido"] = f"frase de cierre prohibida: {hit!r}"

        n_viol = len(violations)
        self.score = max(0.0, 1.0 - 0.25 * n_viol)
        self.success = self.score >= self.threshold
        if violations:
            self.reason = "Violaciones de estilo: " + "; ".join(violations.values())
        else:
            self.reason = "Estilo OK: tuteo, sin em dash, emoji allowlist, cierre sobrio."
        return self.score

    async def a_measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)


def deterministic_metrics(
    *, greeting_threshold: float = 1.0, style_threshold: float = 1.0
) -> list[Any]:
    """Métricas deterministas (sin LLM). Siempre se corren (son gratis)."""
    return [
        GreetingComplianceMetric(threshold=greeting_threshold),
        StyleComplianceMetric(threshold=style_threshold),
    ]


# ---------------------------------------------------------------------------
# Métricas LLM-JUEZ (ConversationalGEval + built-ins).
# ---------------------------------------------------------------------------

def _geval(key: str, name: str, steps: list[str], model: Any, threshold: float) -> Any:
    from deepeval.metrics import ConversationalGEval
    from deepeval.test_case import TurnParams

    # `evaluation_params` solo admite params PER-TURNO (CONTENT/ROLE/TOOLS_CALLED/
    # METADATA/TAGS/RETRIEVAL_CONTEXT). scenario/chatbot_role/context son del test
    # case (ConversationalGEval los usa internamente, NO van acá — pasar
    # CHATBOT_ROLE/SCENARIO/CONTEXT revienta con KeyError). El guion completo ya
    # vive en `evaluation_steps`, así que el juez tiene toda la rúbrica.
    metric = ConversationalGEval(
        name=name,
        evaluation_steps=steps,
        evaluation_params=[
            TurnParams.CONTENT,
            TurnParams.ROLE,
            TurnParams.TOOLS_CALLED,
        ],
        model=model,
        threshold=threshold,
    )
    metric.key = key
    return metric


def script_adherence_metric(model: Any, threshold: float = 0.7) -> Any:
    """LA métrica central: ¿siguió el funnel de 6 fases del guion?"""
    return _geval("script_adherence", "Adherencia al guion de ventas",
                  R.SCRIPT_ADHERENCE_STEPS, model, threshold)


def proactive_offering_metric(model: Any, threshold: float = 0.7) -> Any:
    """¿Ofreció siempre lo que debía (catálogo / productos / siguiente paso)?"""
    return _geval("proactive_offering", "Oferta proactiva",
                  R.PROACTIVE_OFFERING_STEPS, model, threshold)


def no_hallucination_metric(model: Any, threshold: float = 0.8) -> Any:
    """¿Solo afirmó productos/precios que vinieron de una tool? (umbral alto)."""
    return _geval("no_hallucination", "No alucinación de catálogo",
                  R.NO_HALLUCINATION_STEPS, model, threshold)


def conversion_progress_metric(model: Any, threshold: float = 0.6) -> Any:
    """¿Avanzó hacia el cierre sin presionar?"""
    return _geval("conversion_progress", "Avance de conversión",
                  R.CONVERSION_PROGRESS_STEPS, model, threshold)


def correct_handoff_metric(model: Any, threshold: float = 0.7) -> Any:
    """¿Escaló a humano en los disparadores correctos?"""
    return _geval("correct_handoff", "Handoff correcto",
                  R.CORRECT_HANDOFF_STEPS, model, threshold)


def role_adherence_metric(model: Any, threshold: float = 0.7) -> Any:
    """Built-in: ¿se mantuvo en el rol de asesor de ventas Hubara?"""
    from deepeval.metrics import RoleAdherenceMetric

    metric = RoleAdherenceMetric(threshold=threshold, model=model)
    metric.key = "role_adherence"
    return metric


def knowledge_retention_metric(model: Any, threshold: float = 0.7) -> Any:
    """Built-in: ¿NO repreguntó datos que el cliente ya dio? (anti context-leak)."""
    from deepeval.metrics import KnowledgeRetentionMetric

    metric = KnowledgeRetentionMetric(threshold=threshold, model=model)
    metric.key = "knowledge_retention"
    return metric


def judge_metrics(model: Any, *, threshold: float = 0.7) -> list[Any]:
    """Métricas LLM-juez (cuestan llamadas al juez). `model` = juez compartido."""
    return [
        script_adherence_metric(model, threshold),
        proactive_offering_metric(model, threshold),
        no_hallucination_metric(model, 0.8),
        conversion_progress_metric(model, 0.6),
        correct_handoff_metric(model, threshold),
        role_adherence_metric(model, threshold),
        knowledge_retention_metric(model, threshold),
    ]


def all_sales_metrics(model: Any, *, threshold: float = 0.7) -> list[Any]:
    """Deterministas + LLM-juez. Usado por el harness online y los unit tests."""
    return deterministic_metrics() + judge_metrics(model, threshold=threshold)
