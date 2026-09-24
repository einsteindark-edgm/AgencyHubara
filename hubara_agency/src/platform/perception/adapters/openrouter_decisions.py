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
    ) -> None:
        self.model = model
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

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

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
