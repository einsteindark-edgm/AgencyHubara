"""D1.9 — Agent Event de la Meta Business Agent Cloud API.

Contrato (OpenAPI publicado por Meta, v2.0.0, leído 2026-09-08)::

    POST {MBA_API_BASE_URL}/{entity_id}/agent_event
    X-API-Version: 2.0.0  ·  Authorization: Bearer <META_MBA_TOKEN>
    {"to": "+<E.164 del cliente>",
     "event": {"type": "<tipo>", "description": "<qué debe contarle al cliente>",
               "payload": "<JSON como string>"}}
    → {"status": "accepted", "agent_event_id": "..."}   (GET /{agent_event_id} consulta el estado)

``entity_id`` es el ``phone_number_id`` del número onboardeado en MBA (el
mismo que ``agent.yaml`` deja en ``entity_id`` al onboardear). Semántica:
MBA recibe la novedad y se la transmite al cliente con sus palabras, SIN que
Hubara envíe nada por Cloud API (lo que tomaría el hilo, §0.5 del roadmap).
Un ``agent_event`` NO es idempotente: un timeout es ``ambiguous`` (Meta pudo
haberlo aceptado) y no se reintenta acá; el use case lo cuenta como
"posiblemente entregado".
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from src.plugins.mba.adapters.meta_api import (
    MBA_API_BASE_URL,
    MbaApiError,
    mba_token,
    post_json,
)

__all__ = [
    "AGENT_EVENT_API_VERSION",
    "AGENT_EVENT_TIMEOUT_S",
    "AgentEventError",
    "AgentEventPort",
    "AgentEventResult",
    "FakeAgentEvent",
    "MetaAgentEvent",
    "agent_event_url",
]

AGENT_EVENT_API_VERSION = "2.0.0"
#: Por llamada. Peor caso del adapter = 3 × timeout + 1,5 s de backoff
#: (≈13,5 s): por debajo del hop del ETA (15 s) y del start_to_close de su
#: activity de claim (30 s), para que un timeout se vea como tal y no como
#: una activity vencida con el POST aún en vuelo.
AGENT_EVENT_TIMEOUT_S = 4.0


class AgentEventError(MbaApiError):
    pass


@dataclass(frozen=True)
class AgentEventResult:
    agent_event_id: str | None
    status: str


def agent_event_url(base_url: str, entity_id: str) -> str:
    return f"{base_url.rstrip('/')}/{entity_id}/agent_event"


@runtime_checkable
class AgentEventPort(Protocol):
    async def emit(
        self,
        *,
        entity_id: str,
        to: str,
        event_type: str,
        description: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentEventResult: ...


class MetaAgentEvent:
    """Vendor real (httpx, import perezoso). Levanta ``AgentEventError``."""

    def __init__(
        self,
        *,
        token: Callable[[], str] = mba_token,
        base_url: str = MBA_API_BASE_URL,
        timeout_s: float = AGENT_EVENT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._sleep = sleep

    async def emit(
        self,
        *,
        entity_id: str,
        to: str,
        event_type: str,
        description: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentEventResult:
        event: dict[str, Any] = {"type": event_type, "description": description}
        if payload is not None:
            event["payload"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        data = await post_json(
            agent_event_url(self._base_url, entity_id),
            {"to": to, "event": event},
            api_version=AGENT_EVENT_API_VERSION,
            token=self._token(),
            timeout_s=self._timeout_s,
            sleep=self._sleep,
            error_cls=AgentEventError,
            # no idempotente: un timeout es "quizá aceptado" (``ambiguous``), no se reintenta.
            retry_on_transport_error=False,
        )
        agent_event_id = data.get("agent_event_id")
        status = data.get("status")
        return AgentEventResult(
            agent_event_id=str(agent_event_id) if agent_event_id else None,
            status=str(status) if status else "accepted",
        )


@dataclass
class FakeAgentEvent:
    """Registra ``(entity_id, to, type, description, payload)``; ``fail_with`` simula a Meta."""

    calls: list[tuple[str, str, str, str, dict[str, Any] | None]] = field(
        default_factory=list
    )
    fail_with: AgentEventError | None = None

    async def emit(
        self,
        *,
        entity_id: str,
        to: str,
        event_type: str,
        description: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentEventResult:
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append((entity_id, to, event_type, description, payload))
        return AgentEventResult(
            agent_event_id=f"fake-{len(self.calls)}", status="accepted"
        )
