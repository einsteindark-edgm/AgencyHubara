"""Activities de la entrada durable (ADR 2026-10-15): el ingest de un mensaje
y la espera al mensaje anterior del mismo cliente."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError, RPCStatusCode

from src.plugins.chats.agent.sales.contracts import InboundMessageInput, inbound_workflow_id
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.inbound_ledger import message_stage_record
from src.sdk.runtime import with_heartbeat

logger = structlog.get_logger()

#: Tope de la espera al mensaje anterior: un ingest normal tarda segundos; si
#: el anterior se traba, este sale igual (el orden no vale un mensaje perdido).
PREDECESSOR_WAIT_S = 120


@activity.defn(name="ingest_inbound_message")
@with_heartbeat(every=10)
async def ingest_inbound_message_activity(inp: InboundMessageInput) -> str:
    """El ingest de siempre (``IngestInboundMessage.execute``), ahora fuera de la
    API. El mensaje llega como lo dejó el webhook: parseado y con la dirección
    de su conversación (``_in_their_conversation``).

    Deja el desenlace en el ledger de entrada (``ingested`` / ``ingest_failed``,
    lo que antes hacía ``_ingest_ledgered`` en la API). Un fallo se re-lanza:
    Temporal reintenta (tope en el workflow) y el diario de efectos del ingest
    evita repetir lo que el intento anterior ya hizo."""
    from src.plugins.chats.agent.sales import composition

    parsed = WhatsAppMessage(**inp.message)
    try:
        await composition.build_ingest_use_case().execute(parsed)
    except BaseException as exc:
        # También la cancelación (el worker se apaga a mitad): el mensaje NO
        # llegó al bot, se reintenta — nunca queda `ingested`.
        if isinstance(exc, Exception):
            logger.exception("inbound_ingest_failed", wa_message_id=parsed.message_id, attempt=_attempt())
        _ledger(composition, parsed, "ingest_failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _ledger(composition, parsed, "ingested")
    logger.info(
        "inbound_ingested",
        wa_message_id=parsed.message_id,
        attempt=_attempt(),
        waited_ms=int(time.time() * 1000) - inp.received_at_ms if inp.received_at_ms else None,
    )
    return "ingested"


def _attempt() -> int:
    return activity.info().attempt


def _ledger(composition: Any, parsed: WhatsAppMessage, stage: str, *, error: str | None = None) -> None:
    composition.build_inbound_ledger().append(
        [
            message_stage_record(
                stage=stage,
                at_ms=int(time.time() * 1000),
                field="messages",
                wa_message_id=parsed.message_id,
                from_number=parsed.from_number,
                error=error,
            )
        ]
    )


@activity.defn(name="wait_inbound_predecessor")
@with_heartbeat(every=10)
async def wait_inbound_predecessor_activity(wamid: str) -> str:
    """Espera a que cierre el workflow de entrada del mensaje ``wamid``.

    ``done`` (terminó), ``failed`` (terminó mal: igual se sigue), ``not_found``
    (no existe: nunca entró o ya venció la retención) o ``timeout``."""
    handle = activity.client().get_workflow_handle(inbound_workflow_id(wamid))
    try:
        await asyncio.wait_for(handle.result(), timeout=PREDECESSOR_WAIT_S)
    except WorkflowFailureError:
        return "failed"
    except TimeoutError:
        return "timeout"
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return "not_found"
        raise
    return "done"
