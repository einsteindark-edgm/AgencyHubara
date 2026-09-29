"""Capacidades del motor de decisiones (diseño v2 §07, fase F2).

Cada pieza de código quemado (una regla de texto que intenta entender lo que
escribió el cliente o el LLM) pasa a ser una CAPACIDAD:

  rule(inp)                          la regla de hoy, intacta (respaldo)
  ask(inp) -> (state, preguntas)     las preguntas cerradas a Jev (o None:
                                     no hace falta preguntar)
  decide(inp, result, rule, th)      la política: respuestas → decisión, o
                                     None si duda (decide la regla)
  floor(inp, rule, jev)              el piso: lo que nunca se quita (p. ej.
                                     la baja explícita)
  same(a, b)                         si dos decisiones coinciden

`decide()` corre la capacidad con el proveedor que dice el registro de bots
(`reglas`, `sombra` o `jev`). El consumidor (tool, activity, ingest) no sabe
quién contestó: recibe un `Verdict` con el valor y la traza.

Sin Temporal: lo usan activities, el ingest y (vía `guards`) las tools.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()

BY_RULE = "reglas"
BY_JEV = "jev"
BY_FLOOR = "piso"
BY_FALLBACK = "respaldo"


class Capability(Protocol):
    name: str
    timeout_s: float

    def rule(self, inp: Any) -> Any: ...

    def ask(self, inp: Any) -> tuple[str, Sequence[Any]] | None: ...

    def decide(self, inp: Any, result: Any, rule: Any, thresholds: Mapping[str, float]) -> Any | None: ...

    def floor(self, inp: Any, rule: Any, jev: Any) -> Any: ...

    def same(self, a: Any, b: Any) -> bool: ...


@dataclass(frozen=True)
class Verdict:
    capability: str
    value: Any
    by: str  # reglas | jev | piso | respaldo
    provider: str  # reglas | sombra | jev
    rule: Any
    jev: Any = None
    agree: bool | None = None
    reason: str | None = None  # por qué decidió la regla en modo jev
    model: str = ""
    latency_ms: int = 0
    answers: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_trace(self) -> dict[str, Any]:
        """JSON para la traza y el laboratorio."""
        return {
            "capability": self.capability,
            "value": self.value,
            "by": self.by,
            "provider": self.provider,
            "rule": self.rule,
            "jev": self.jev,
            "agree": self.agree,
            "reason": self.reason,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "answers": list(self.answers),
        }


#: Quienes miran las decisiones de este proceso (el sandbox del laboratorio,
#: que las publica con su turno). En producción nadie mira: `decide` es el de
#: siempre.
_WATCHERS: list[Callable[[Verdict], None]] = []


@contextmanager
def watching_verdicts(watcher: Callable[[Verdict], None]) -> Iterator[None]:
    """Mientras dure el bloque, `watcher` ve cada decisión de este proceso
    (`decide`: ingest, tools y activities), en el orden en que se tomaron."""
    _WATCHERS.append(watcher)
    try:
        yield
    finally:
        _WATCHERS.remove(watcher)


def _tell_watchers(verdict: Verdict) -> None:
    for watcher in tuple(_WATCHERS):
        try:
            watcher(verdict)
        except Exception as exc:  # noqa: BLE001 — mirar nunca frena la decisión
            logger.warning("decisions.watcher_failed", capability=verdict.capability, error=repr(exc)[:200])


def _answers(result: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        {"q": a.id, "type": a.kind, "p": a.p, "choice": a.choice, "confidence": a.confidence}
        for a in getattr(result, "answers", ()) or ()
    )


def _thresholds(capability: Any) -> dict[str, float]:
    return dict(getattr(capability, "thresholds", {}) or {})


async def _ask_oracle(profile: Any, state: str, questions: Sequence[Any], *, timeout_s: float, redact: Sequence[str]) -> Any:
    from src.sdk.connectorkit import PerceptionResult, get_perception_port, oracle_timeout_s

    timeout = min(float(timeout_s), oracle_timeout_s(profile.oracle))
    try:
        port = get_perception_port(profile.oracle)
        return await asyncio.wait_for(port.ask(state, questions, timeout_s=timeout, redact=redact), timeout=timeout + 0.25)
    except TimeoutError:
        return PerceptionResult(ok=False, error="timeout", provider="motor", model="")
    except Exception as exc:  # noqa: BLE001 — Jev nunca tumba al consumidor
        logger.warning("decisions.capability_oracle_error", error=repr(exc)[:200])
        return PerceptionResult(ok=False, error="unexpected", provider="motor", model="")


async def decide(
    capability: Any,
    inp: Any,
    *,
    provider: str,
    profile_id: str,
    disagreements: Any = None,
    session_id: str | None = None,
    redact: Sequence[str] = (),
    metrics: Any = None,
) -> Verdict:
    """La decisión de la capacidad con el proveedor del bot. Nunca lanza por
    Jev; la regla sí puede lanzar (igual que hoy)."""
    verdict = await _decide(
        capability, inp, provider=provider, profile_id=profile_id, disagreements=disagreements,
        session_id=session_id, redact=redact, metrics=metrics,
    )
    if _WATCHERS:
        _tell_watchers(verdict)
    return verdict


async def _decide(
    capability: Any,
    inp: Any,
    *,
    provider: str,
    profile_id: str,
    disagreements: Any,
    session_id: str | None,
    redact: Sequence[str],
    metrics: Any,
) -> Verdict:
    from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile

    name = str(capability.name)
    rule = capability.rule(inp)
    if provider not in ("sombra", "jev"):
        return Verdict(capability=name, value=rule, by=BY_RULE, provider="reglas", rule=rule)
    fallback_by = BY_RULE if provider == "sombra" else BY_FALLBACK
    profile = get_engine_profile(profile_id)
    if profile is None:
        return Verdict(capability=name, value=rule, by=fallback_by, provider=provider, rule=rule, reason="unknown_profile")
    asked = capability.ask(inp)
    if asked is None:
        return Verdict(capability=name, value=rule, by=fallback_by, provider=provider, rule=rule, reason="no_question")
    state, questions = asked
    result = await _ask_oracle(profile, state, questions, timeout_s=float(capability.timeout_s), redact=redact)
    jev = capability.decide(inp, result, rule, _thresholds(capability)) if result.ok else None
    agree = None if jev is None else bool(capability.same(rule, jev))
    base = dict(
        capability=name, provider=provider, rule=rule, jev=jev, agree=agree, model=str(result.model or ""),
        latency_ms=int(getattr(result, "latency_ms", 0) or 0), answers=_answers(result),
    )
    if metrics is not None:
        try:
            metrics.record(capability=name, provider=provider, ok=bool(result.ok), latency_ms=base["latency_ms"], agree=agree)
        except Exception as exc:  # noqa: BLE001 — medir nunca frena la decisión
            logger.warning("decisions.metric_not_recorded", capability=name, error=repr(exc)[:200])
    if jev is not None and not agree and disagreements is not None:
        try:
            disagreements.record(
                capability=name, state=state, rule=rule, jev=jev, model=base["model"], answers=list(base["answers"]),
                session_id=session_id, redact=redact,
            )
        except Exception as exc:  # noqa: BLE001 — la cola nunca frena la decisión
            logger.warning("decisions.disagreement_not_recorded", capability=name, error=repr(exc)[:200])
    if provider == "sombra":
        return Verdict(value=rule, by=BY_RULE, **base)
    if not result.ok:
        return Verdict(value=rule, by=BY_FALLBACK, reason=result.error or "error", **base)
    if profile.calibrated_model and result.model != profile.calibrated_model:
        return Verdict(value=rule, by=BY_FALLBACK, reason="model_changed", **base)
    if jev is None:
        return Verdict(value=rule, by=BY_FALLBACK, reason="duda", **base)
    final = capability.floor(inp, rule, jev)
    return Verdict(value=final, by=BY_JEV if capability.same(final, jev) else BY_FLOOR, **base)
