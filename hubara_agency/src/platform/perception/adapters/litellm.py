"""OpenAI con logprobs, por OpenRouter vía LiteLLM — brazo C del laboratorio.

Cada pregunta se responde con un código de UN token (plan §1.3):

* `noul`: `S` (sí) o `N` (no);
* `choice`: una letra `A`, `B`, `C`… por opción, en el orden de los criterios;
* `score`: un dígito `0`…`9` por nivel de la rúbrica.

El modelo escribe un código por línea, en el orden de las preguntas. La
probabilidad sale de `top_logprobs` en la posición de cada código,
renormalizada sobre los códigos PERMITIDOS de esa pregunta (un "Si" o un
espacio no cuentan). Opcional: calibración por pregunta con temperature
scaling, ajustada en la arena del laboratorio. Así las respuestas tienen la
misma forma que las de Jev y se comparan con Brier y error de calibración.

Sin logprobs no hay probabilidad honesta: `ok=False` con `no_logprobs` (con
`require_parameters` en el alias, OpenRouter rechaza la llamada antes). Nunca
lanza: timeout o error del proveedor devuelven `ok=False`.
"""
from __future__ import annotations

import asyncio
import math
import os
import string
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import structlog

from src.platform.perception.adapters._validate import BadShape
from src.platform.perception.anonymize import anonymize_text
from src.platform.perception.ports import (
    ERROR_BAD_SHAPE,
    ERROR_NO_LOGPROBS,
    ERROR_PROVIDER,
    ERROR_TIMEOUT,
    PerceptionResult,
    TypedAnswer,
    TypedQuestion,
    failed,
)

logger = structlog.get_logger()

Completion = Callable[..., Awaitable[Any]]

_NOUL_CODES = ("S", "N")
_EPS = 1e-6

SYSTEM_PROMPT = (
    "Eres un clasificador de conversaciones de venta por WhatsApp. No conversas: "
    "respondes SOLO con códigos, uno por línea, en el orden de las preguntas, sin "
    "números de pregunta, sin puntos y sin explicaciones."
)


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def codes_for(q: TypedQuestion) -> tuple[str, ...]:
    if q.kind == "noul":
        return _NOUL_CODES
    if q.kind == "choice":
        if len(q.options) > 26:
            raise BadShape(f"{q.id}: más de 26 opciones")
        return tuple(string.ascii_uppercase[: len(q.options)])
    if len(q.options) > 10:
        raise BadShape(f"{q.id}: más de 10 niveles")
    return tuple(str(i) for i in range(len(q.options)))


def build_prompt(state: str, questions: Sequence[TypedQuestion]) -> str:
    lines = ["CONVERSACIÓN:", state, "", "PREGUNTAS:"]
    for n, q in enumerate(questions, 1):
        lines.append(f"{n}. {q.text}")
        if q.kind == "noul":
            crit = dict(q.criteria) if isinstance(q.criteria, Mapping) else {}
            lines.append(f"   S = sí{': ' + crit['true'] if crit.get('true') else ''}")
            lines.append(f"   N = no{': ' + crit['false'] if crit.get('false') else ''}")
        elif q.kind == "choice":
            crit = dict(q.criteria) if isinstance(q.criteria, Mapping) else {o: "" for o in q.options}
            for code, option in zip(codes_for(q), q.options):
                desc = crit.get(option) or ""
                lines.append(f"   {code} = {option}{': ' + desc if desc else ''}")
        else:
            for code, level in zip(codes_for(q), q.options):
                lines.append(f"   {code} = {level}")
    lines += ["", f"Responde con {len(questions)} líneas: el código de cada pregunta, en orden."]
    return "\n".join(lines)


def _distribution(token_info: Any, allowed: tuple[str, ...]) -> dict[str, float]:
    best: dict[str, float] = {}
    for top in _get(token_info, "top_logprobs") or []:
        code = str(_get(top, "token") or "").strip()
        lp = _get(top, "logprob")
        if code in allowed and isinstance(lp, (int, float)):
            best[code] = max(best.get(code, -math.inf), float(lp))
    generated = str(_get(token_info, "token") or "").strip()
    lp = _get(token_info, "logprob")
    if generated in allowed and generated not in best and isinstance(lp, (int, float)):
        best[generated] = float(lp)
    if not best:
        raise BadShape("sin probabilidad para ningún código permitido")
    top = max(best.values())
    weights = {c: math.exp(v - top) for c, v in best.items()}
    total = sum(weights.values())
    return {c: weights.get(c, 0.0) / total for c in allowed}


def _temper(dist: dict[str, float], temperature: float | None) -> dict[str, float]:
    """Temperature scaling: p_i ∝ p_i^(1/T). T > 1 aplana, T < 1 afila."""
    if not temperature or temperature <= 0 or temperature == 1:
        return dist
    powered = {c: max(p, _EPS) ** (1.0 / temperature) for c, p in dist.items()}
    total = sum(powered.values())
    return {c: v / total for c, v in powered.items()}


def _answer(q: TypedQuestion, dist: dict[str, float]) -> TypedAnswer:
    if q.kind == "noul":
        return TypedAnswer(id=q.id, kind="noul", p=dist["S"])
    codes = codes_for(q)
    if q.kind == "choice":
        probs = tuple((option, dist[code]) for code, option in zip(codes, q.options))
        choice, conf = max(probs, key=lambda kv: kv[1])
        return TypedAnswer(id=q.id, kind="choice", choice=choice, probs=probs, confidence=conf)
    probs = tuple((code, dist[code]) for code in codes)
    score = sum(int(code) * p for code, p in probs)
    return TypedAnswer(id=q.id, kind="score", score=score, probs=probs, confidence=max(p for _, p in probs))


class LiteLLMLogprobsAdapter:
    name = "litellm"

    def __init__(
        self,
        model: str,
        *,
        completion: Completion | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        params: Mapping[str, Any] | None = None,
        anonymize: bool = False,
        calibration: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        self.model = model
        self._completion = completion
        self._api_base = api_base
        self._api_key = api_key
        self._params = dict(params or {"logprobs": True, "top_logprobs": 20, "temperature": 0, "max_tokens": 64})
        self._anonymize = anonymize
        self._calibration = {k: dict(v) for k, v in (calibration or {}).items()}

    @classmethod
    def from_profile(cls, profile: Any) -> "LiteLLMLogprobsAdapter":
        from src.platform.config import API_BASE_LLMLITE, LITELLM_API_KEY

        return cls(
            profile.model,
            api_base=os.getenv("PERCEPTION_API_BASE") or API_BASE_LLMLITE,
            api_key=os.getenv("LITELLM_API_KEY") or LITELLM_API_KEY,
            params=profile.params,
            anonymize=profile.anonymize,
            calibration=profile.calibration,
        )

    async def _call(self, **kwargs: Any) -> Any:
        if self._completion is not None:
            return await self._completion(**kwargs)
        import litellm  # tardío: el SDK es lazy (test_sdk_lazy_surface)

        return await litellm.acompletion(**kwargs)

    async def ask(
        self,
        state: str,
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        redact: Sequence[str] = (),
    ) -> PerceptionResult:
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            allowed = [codes_for(q) for q in questions]
        except BadShape:
            return failed(ERROR_BAD_SHAPE, provider=self.name, model=self.model)
        text = anonymize_text(state, redact=redact) if self._anonymize else state
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(text, questions)},
            ],
            "timeout": timeout_s,
            **self._params,
        }
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._api_key:
            kwargs["api_key"] = self._api_key
        try:
            response = await asyncio.wait_for(self._call(**kwargs), timeout=timeout_s)
        except asyncio.TimeoutError:
            return failed(ERROR_TIMEOUT, provider=self.name, model=self.model, latency_ms=elapsed())
        except Exception as exc:  # noqa: BLE001 — fail-open: el turno sigue como hoy
            error = ERROR_TIMEOUT if "timeout" in type(exc).__name__.lower() else ERROR_PROVIDER
            logger.warning("perception.litellm.error", error=repr(exc)[:200])
            return failed(error, provider=self.name, model=self.model, latency_ms=elapsed())
        choices = _get(response, "choices") or []
        logprobs = _get(choices[0], "logprobs") if choices else None
        content = _get(logprobs, "content") if logprobs is not None else None
        if not content:
            return failed(ERROR_NO_LOGPROBS, provider=self.name, model=self.model, latency_ms=elapsed())
        tokens = [t for t in content if str(_get(t, "token") or "").strip()]
        try:
            if len(tokens) < len(questions):
                raise BadShape("faltan respuestas")
            answers = []
            for q, codes, token in zip(questions, allowed, tokens):
                if str(_get(token, "token") or "").strip() not in codes:
                    raise BadShape(f"{q.id}: código fuera de los permitidos")
                dist = _temper(_distribution(token, codes), self._calibration.get(q.id, {}).get("temperature"))
                answers.append(_answer(q, dist))
        except BadShape as exc:
            logger.warning("perception.litellm.bad_shape", error=str(exc)[:200])
            return failed(ERROR_BAD_SHAPE, provider=self.name, model=self.model, latency_ms=elapsed())
        usage = _get(response, "usage") or {}
        return PerceptionResult(
            ok=True,
            answers=tuple(answers),
            provider=self.name,
            model=str(_get(response, "model") or self.model),
            latency_ms=elapsed(),
            input_tokens=_get(usage, "prompt_tokens"),
            output_tokens=_get(usage, "completion_tokens"),
        )
