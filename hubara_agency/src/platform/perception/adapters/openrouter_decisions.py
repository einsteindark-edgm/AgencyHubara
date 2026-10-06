"""Jev (TypeSafe) por la Decisions API de OpenRouter — brazo B del laboratorio.

`POST https://openrouter.ai/api/alpha/decisions` con `{model, state,
questions}`: no es la API de chat, así que Jev NO pasa por LiteLLM (plan §1.3).
Las preguntas ya tienen la forma nativa (`noul`, `choice`, `score`). Viajan con
claves `q0`, `q1`… y se mapean de vuelta a los ids del puerto: un id con
puntos o tildes nunca depende de lo que acepte una API en alpha.

Modelo FIJO (`typesafe/jev-1.13`, L-23). La respuesta dice qué snapshot la
sirvió (`typesafe/jev-1.13-20260917`): eso queda en `PerceptionResult.model`.
Fail-open: timeout, error HTTP (402 sin crédito, 429, 5xx) o una respuesta con
otra forma (la API está en alpha) devuelven `ok=False` sin lanzar.

Reenvío de una petición colgada (laboratorio, 2026-09-29): ~1 de cada 10
llamadas se colgaba más de 10 s mientras el resto respondía en medio segundo.
Si no hay respuesta a cada uno de los `resend_after_s` del perfil, sale un
reenvío y gana la primera respuesta válida; las demás se cancelan. Todo dentro
de la MISMA espera (`timeout_s`).
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import structlog

from src.platform.perception.adapters._validate import BadShape, check_answer
from src.platform.perception.anonymize import anonymize_text
from src.platform.perception.ports import (
    ERROR_BAD_SHAPE,
    ERROR_NO_API_KEY,
    ERROR_PROVIDER,
    ERROR_TIMEOUT,
    PerceptionResult,
    TypedQuestion,
    failed,
)

logger = structlog.get_logger()

DEFAULT_BASE_URL = "https://openrouter.ai"
DECISIONS_PATH = "/api/alpha/decisions"
# Terraform crea el secreto con este valor hasta que el operador carga la llave.
_SSM_PLACEHOLDER_PREFIX = "PLACEHOLDER"


def _real_key(value: str | None) -> str | None:
    key = (value or "").strip()
    return None if not key or key.startswith(_SSM_PLACEHOLDER_PREFIX) else key


def _question_body(q: TypedQuestion) -> dict[str, Any]:
    criteria: Any = list(q.criteria) if not isinstance(q.criteria, Mapping) else dict(q.criteria)
    body: dict[str, Any] = {"type": q.kind, "instructions": q.text}
    if criteria:
        body["criteria"] = criteria
    return body


class OpenRouterDecisionsAdapter:
    name = "openrouter_decisions"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str = DEFAULT_BASE_URL,
        provider_prefs: Mapping[str, Any] | None = None,
        anonymize: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        resend_after_s: Sequence[float] = (),
    ) -> None:
        self.model = model
        self._resend_after_s = tuple(float(s) for s in resend_after_s)
        self._api_key = _real_key(api_key)
        self._url = base_url.rstrip("/") + DECISIONS_PATH
        self._provider_prefs = dict(provider_prefs or {})
        self._anonymize = anonymize
        self._transport = transport

    @property
    def has_api_key(self) -> bool:
        return self._api_key is not None

    @classmethod
    def from_profile(cls, profile: Any) -> "OpenRouterDecisionsAdapter":
        return cls(
            model=profile.model,
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            base_url=os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL,
            provider_prefs=profile.provider_prefs,
            anonymize=profile.anonymize,
            resend_after_s=getattr(profile, "resend_after_s", ()),
        )

    async def ask(
        self,
        state: str,
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        redact: Sequence[str] = (),
    ) -> PerceptionResult:
        started = time.monotonic()
        if not self._api_key:
            return failed(ERROR_NO_API_KEY, provider=self.name, model=self.model)
        keys = [f"q{i}" for i in range(len(questions))]
        body: dict[str, Any] = {
            "model": self.model,
            "state": anonymize_text(state, redact=redact) if self._anonymize else state,
            "questions": {k: _question_body(q) for k, q in zip(keys, questions)},
        }
        if self._provider_prefs:
            body["provider"] = self._provider_prefs
        resends = [s for s in self._resend_after_s if 0 < s < timeout_s]
        if not resends:
            return await self._post(body, keys, questions, timeout_s=timeout_s, started=started)
        return await self._with_resends(body, keys, questions, timeout_s=timeout_s, resends=resends, started=started)

    async def _with_resends(
        self,
        body: dict[str, Any],
        keys: list[str],
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        resends: list[float],
        started: float,
    ) -> PerceptionResult:
        """La primera respuesta válida entre la original y sus reenvíos, dentro
        de `timeout_s`. Una falla (que no sea el tiempo) sin otro intento en
        vuelo se devuelve de una: la reintenta quien pregunta si es pasajera."""
        deadline = started + timeout_s
        attempts: list[asyncio.Task[PerceptionResult]] = []

        def launch() -> None:
            remaining = max(deadline - time.monotonic(), 0.01)
            attempts.append(asyncio.create_task(self._post(body, keys, questions, timeout_s=remaining, started=started)))

        launch()
        pending_resends = list(resends)
        last: PerceptionResult | None = None
        try:
            while True:
                now = time.monotonic()
                in_flight = {a for a in attempts if not a.done()}
                next_at = started + pending_resends[0] if pending_resends else deadline
                if not in_flight and last is not None and not last.ok:
                    return last
                if in_flight:
                    done, _ = await asyncio.wait(
                        in_flight, timeout=max(min(next_at, deadline) - now, 0), return_when=asyncio.FIRST_COMPLETED
                    )
                    for attempt in done:
                        result = attempt.result()
                        if result.ok:
                            if len(attempts) > 1:
                                logger.info("perception.decisions.resend_won", attempts=len(attempts))
                            return result
                        last = result
                    if done:
                        continue
                if time.monotonic() >= deadline:
                    return failed(ERROR_TIMEOUT, provider=self.name, model=self.model, latency_ms=int(timeout_s * 1000))
                if pending_resends and time.monotonic() >= started + pending_resends[0]:
                    pending_resends.pop(0)
                    logger.info("perception.decisions.resend", attempt=len(attempts) + 1)
                    launch()
        finally:
            for attempt in attempts:
                if not attempt.done():
                    attempt.cancel()

    async def _post(
        self,
        body: dict[str, Any],
        keys: list[str],
        questions: Sequence[TypedQuestion],
        *,
        timeout_s: float,
        started: float,
    ) -> PerceptionResult:
        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout_s) as client:
                response = await asyncio.wait_for(
                    client.post(
                        self._url,
                        json=body,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    ),
                    timeout=timeout_s,
                )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return failed(ERROR_TIMEOUT, provider=self.name, model=self.model, latency_ms=elapsed())
        except httpx.HTTPError as exc:
            logger.warning("perception.decisions.transport_error", error=repr(exc)[:200])
            return failed(ERROR_PROVIDER, provider=self.name, model=self.model, latency_ms=elapsed())
        if response.status_code != 200:
            logger.warning("perception.decisions.http_error", status=response.status_code)
            return failed(f"http_{response.status_code}", provider=self.name, model=self.model, latency_ms=elapsed())
        try:
            data = response.json()
            raw_answers = data["answers"]
            if not isinstance(raw_answers, dict):
                raise BadShape("answers no es un objeto")
            answers = tuple(check_answer(q, raw_answers.get(k)) for k, q in zip(keys, questions))
        except (BadShape, KeyError, TypeError, ValueError) as exc:
            logger.warning("perception.decisions.bad_shape", error=str(exc)[:200])
            return failed(ERROR_BAD_SHAPE, provider=self.name, model=self.model, latency_ms=elapsed())
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost")
        return PerceptionResult(
            ok=True,
            answers=answers,
            provider=self.name,
            model=str(data.get("model") or self.model),
            latency_ms=elapsed(),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            input_tokens=usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None,
            output_tokens=usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None,
        )
