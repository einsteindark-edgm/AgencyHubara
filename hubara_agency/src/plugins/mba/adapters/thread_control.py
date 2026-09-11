"""D1.6 — Thread Control de la Meta Business Agent Cloud API.

Contrato (OpenAPI publicado por Meta, v1.0.0, 2026-09-04)::

    POST {MBA_API_BASE_URL}/business/whatsapp/phone_numbers/{phone_number_id}/thread_control
    X-API-Version: 1.0.0  ·  Authorization: Bearer <META_MBA_TOKEN>
    {"messaging_product": "whatsapp", "action": "release"|"take"|"pass",
     "to": "<teléfono o WA ID del cliente>", "metadata": "<≤2000 chars, viaja
     verbatim en el messaging_handovers resultante>"}
    → {"messaging_product": "whatsapp"}

Semántica de Meta: ``release`` devuelve el hilo a Business Agent (hay que
TENER el control); ``take`` está restringido al escalation partner
configurado; ``pass`` (``control_pass.target_role=ai_agent``) equivale a
release. Hubara usa ``release``; ``take`` queda expuesto por completitud.
Nota: este endpoint solo enumera ``1.0.0`` (los de configuración usan 2.0.0).
``metadata`` se trunca a 2000 chars (tope de Meta); el use case ya lo acota
con su prefijo incluido.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from src.plugins.mba.adapters.meta_api import (
    DEFAULT_TIMEOUT_S,
    MBA_API_BASE_URL,
    MbaApiError,
    mba_token,
    post_json,
)

__all__ = [
    "THREAD_CONTROL_API_VERSION",
    "FakeThreadControl",
    "MetaThreadControl",
    "ThreadControlError",
    "ThreadControlPort",
    "ThreadControlResult",
    "thread_control_url",
]

THREAD_CONTROL_API_VERSION = "1.0.0"
_METADATA_MAX = 2000


class ThreadControlError(MbaApiError):
    pass


@dataclass(frozen=True)
class ThreadControlResult:
    action: str
    to: str


def thread_control_url(base_url: str, phone_number_id: str) -> str:
    return f"{base_url.rstrip('/')}/business/whatsapp/phone_numbers/{phone_number_id}/thread_control"


@runtime_checkable
class ThreadControlPort(Protocol):
    async def release(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult: ...

    async def take(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult: ...


class MetaThreadControl:
    """Vendor real (httpx, import perezoso). Levanta ``ThreadControlError``."""

    def __init__(
        self,
        *,
        token: Callable[[], str] = mba_token,
        base_url: str = MBA_API_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._sleep = sleep

    async def _act(
        self, action: str, phone_number_id: str, to: str, metadata: str | None
    ) -> ThreadControlResult:
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "action": action,
            "to": to,
        }
        if metadata:
            body["metadata"] = metadata[:_METADATA_MAX]
        await post_json(
            thread_control_url(self._base_url, phone_number_id),
            body,
            api_version=THREAD_CONTROL_API_VERSION,
            token=self._token(),
            timeout_s=self._timeout_s,
            sleep=self._sleep,
            error_cls=ThreadControlError,
            # release/take NO son idempotentes: un timeout es "quizá hecho"
            # (``ambiguous``), no se reintenta.
            retry_on_transport_error=False,
        )
        return ThreadControlResult(action=action, to=to)

    async def release(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult:
        return await self._act("release", phone_number_id, to, metadata)

    async def take(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult:
        return await self._act("take", phone_number_id, to, metadata)


@dataclass
class FakeThreadControl:
    """Registra ``(action, phone_number_id, to, metadata)``; ``fail_with`` simula a Meta."""

    calls: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    fail_with: ThreadControlError | None = None

    async def _act(
        self, action: str, phone_number_id: str, to: str, metadata: str | None
    ) -> ThreadControlResult:
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((action, phone_number_id, to, metadata))
        return ThreadControlResult(action=action, to=to)

    async def release(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult:
        return await self._act("release", phone_number_id, to, metadata)

    async def take(
        self, *, phone_number_id: str, to: str, metadata: str | None = None
    ) -> ThreadControlResult:
        return await self._act("take", phone_number_id, to, metadata)
