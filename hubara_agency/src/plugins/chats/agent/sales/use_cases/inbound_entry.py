"""Entrada durable de los mensajes de WhatsApp (ADR 2026-10-15 §1).

Lo que el webhook hace ANTES de responderle 200 a Meta: dejar cada mensaje en
Temporal como un ``InboundMessageWorkflow`` con id ``inbound-<wamid>``. Desde
ahí el mensaje sobrevive a un deploy o a un OOM de la API (antes vivía en un
``BackgroundTask`` y Meta ya no reintentaba).

- ``REJECT_DUPLICATE``: una re-entrega de Meta del mismo ``wamid`` choca con el
  id (mientras dure la retención del namespace) y ``accept`` devuelve ``False``:
  ya estaba adentro, no es un error.
- Cualquier otra falla sube: el webhook responde 503 y Meta reintenta el POST.
- El cliente de Temporal se conecta una vez y se reusa: ahora la conexión queda
  antes del 200, y abrir una por mensaje (lo de antes) era latencia y memoria.
  Tras una falla se descarta y la próxima llamada reconecta.

R-DIP: use case con el cliente inyectado (``client_factory``); el workflow se
arranca por NOMBRE (string), sin importar su clase (ADR-2026-05-19).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from src.plugins.chats.agent.sales.contracts import InboundMessageInput, inbound_workflow_id

INBOUND_WORKFLOW = "InboundMessageWorkflow"
#: Tope del RPC a Temporal: el webhook no deja a Meta esperando más que esto
#: por mensaje (Meta corta y reintenta).
_RPC_TIMEOUT = timedelta(seconds=8)


class InboundEntry:
    def __init__(self, client_factory: Callable[[], Awaitable[Any]], *, task_queue: str) -> None:
        self._client_factory = client_factory
        self._task_queue = task_queue
        self._client: Any = None
        self._lock = asyncio.Lock()

    async def accept(self, inp: InboundMessageInput) -> bool:
        """``True`` si el mensaje entró ahora; ``False`` si ya estaba (re-entrega)."""
        client = await self._connected()
        try:
            await client.start_workflow(
                INBOUND_WORKFLOW,
                inp,
                id=inbound_workflow_id(str(inp.message.get("message_id") or "")),
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=_RPC_TIMEOUT,
            )
        except WorkflowAlreadyStartedError:
            return False
        except Exception:
            self._client = None
            raise
        return True

    async def _connected(self) -> Any:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = await asyncio.wait_for(
                        self._client_factory(), timeout=_RPC_TIMEOUT.total_seconds()
                    )
        return self._client
