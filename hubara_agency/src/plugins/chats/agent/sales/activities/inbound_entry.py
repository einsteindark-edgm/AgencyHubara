"""Activities de la entrada durable (ADR 2026-10-15).

Fase 2: cada paso del ingest es una activity (`read_inbound_state`,
`prepare_inbound_turn`, `write_inbound_history`, `deliver_inbound_turn`, y la
foto y la nota de voz con la suya), más el anuncio al `InboundSessionWorkflow`
del cliente y la espera al workflow del mensaje anterior. El desenlace de cada
mensaje queda en el ledger de entrada (`ingested` / `ingest_failed`).

Fase 1 (runs que estaban vivos al desplegar la fase 2): el ingest entero en
una activity y la espera al anterior del mismo POST."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError, RPCStatusCode

from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales.contracts import (
    TURN_IGNORED,
    InboundArrival,
    InboundMessageInput,
    InboundSessionState,
    IngestRequest,
    IngestState,
    IngestTurn,
    inbound_session_workflow_id,
    inbound_workflow_id,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage, message_from_dict
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

    wamid = str(inp.message.get("message_id") or "")
    from_number = str(inp.message.get("from_number") or "")
    # La conversación en cada log: con el wamid ubica el workflow `inbound-<wamid>`.
    log = logger.bind(wa_message_id=wamid, session=f"wa_{from_number}", attempt=_attempt())
    try:
        # Rehidratar también va adentro: un input roto deja `ingest_failed` con
        # su wamid (premortem PM-005).
        await composition.build_ingest_use_case().execute(
            _message(inp.message), received_at_ms=inp.received_at_ms or None
        )
    except BaseException as exc:
        # También la cancelación (el worker se apaga a mitad): el mensaje NO
        # llegó al bot, se reintenta — nunca queda `ingested`.
        if isinstance(exc, Exception):
            log.exception("inbound_ingest_failed")
        _ledger(composition, wamid, from_number, "ingest_failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _ledger(composition, wamid, from_number, "ingested")
    log.info(
        "inbound_ingested",
        waited_ms=int(time.time() * 1000) - inp.received_at_ms if inp.received_at_ms else None,
    )
    return "ingested"


def _message(raw: dict[str, Any]) -> WhatsAppMessage:
    """El mensaje del input, tolerante a la versión que lo encoló."""
    return message_from_dict(raw)


def _attempt() -> int:
    return activity.info().attempt


def _ledger(composition: Any, wamid: str, from_number: str, stage: str, *, error: str | None = None) -> None:
    composition.build_inbound_ledger().append(
        [
            message_stage_record(
                stage=stage,
                at_ms=int(time.time() * 1000),
                field="messages",
                wa_message_id=wamid,
                from_number=from_number,
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


# --- Fase 2: un paso del ingest por activity --------------------------------

#: La reentrada de una foto o de una nota de voz lleva el wamid con sufijo; el
#: ledger cuenta el mensaje que llegó al webhook.
_SYNTHETIC_SUFFIXES = ("_transcribed", "_vision")


def _ledger_message(request: IngestRequest) -> tuple[str, str]:
    """`(wamid, from_number)` del mensaje del webhook que cuenta el ledger (sin
    el sufijo de la reentrada). Sin rehidratar: un input roto igual deja su
    marca (premortem PM-005)."""
    wamid = str(request.message.get("message_id") or "")
    for suffix in _SYNTHETIC_SUFFIXES:
        wamid = wamid.removesuffix(suffix)
    return wamid, str(request.message.get("from_number") or "")


async def _step(request: IngestRequest, step: str, run: Any) -> Any:
    """Corre un paso del ingest. Si falla (también si lo cancelan: el worker se
    apaga a mitad), deja `ingest_failed` en el ledger y re-lanza: Temporal
    reintenta ESE paso, no los anteriores (su resultado ya está en el
    historial del workflow)."""
    from src.plugins.chats.agent.sales import composition

    try:
        return await run(composition.build_ingest_use_case())
    except BaseException as exc:
        wamid, from_number = _ledger_message(request)
        if isinstance(exc, Exception):
            logger.exception(
                "inbound_step_failed", step=step, wa_message_id=wamid, session=f"wa_{from_number}", attempt=_attempt()
            )
        _ledger(composition, wamid, from_number, "ingest_failed", error=f"{step}: {type(exc).__name__}: {exc}")
        raise


def _ingested(request: IngestRequest) -> None:
    """El mensaje terminó su camino (llegó al bot, quedó solo en el chat o no
    tenía nada que escribir)."""
    from src.plugins.chats.agent.sales import composition

    wamid, from_number = _ledger_message(request)
    _ledger(composition, wamid, from_number, "ingested")
    logger.info(
        "inbound_ingested",
        wa_message_id=wamid,
        session=f"wa_{from_number}",
        attempt=_attempt(),
        waited_ms=int(time.time() * 1000) - request.received_at_ms if request.received_at_ms else None,
    )


@activity.defn(name="read_inbound_state")
@with_heartbeat(every=10)
async def read_inbound_state_activity(request: IngestRequest) -> IngestState:
    """Paso 1: el estado de la sesión con sus lecturas (Jev). Si la escritura
    del estado falla, el paso se reintenta: el episodio y las lecturas no se
    pierden."""
    state = await _step(request, "state", lambda ingest: ingest.read_state(request))
    if state.write_failed:
        raise ApplicationError("no se pudo escribir el estado de la sesión", type="InboundStateWriteFailed")
    return state


@activity.defn(name="prepare_inbound_turn")
@with_heartbeat(every=10)
async def prepare_inbound_turn_activity(request: IngestRequest, state: IngestState) -> IngestTurn:
    """Paso 2: el carrito y el cupón; el turno armado o por qué todavía no hay."""
    turn = await _step(request, "turn", lambda ingest: ingest.prepare_turn(request, state))
    if turn.outcome == TURN_IGNORED:
        _ingested(request)
    return turn


@activity.defn(name="write_inbound_history")
@with_heartbeat(every=10)
async def write_inbound_history_activity(request: IngestRequest, state: IngestState, turn: IngestTurn) -> str:
    """Paso 3: la línea del cliente en el historial del panel."""
    await _step(request, "history", lambda ingest: ingest.write_history(request, state, turn))
    return "written"


@activity.defn(name="deliver_inbound_turn")
@with_heartbeat(every=10)
async def deliver_inbound_turn_activity(request: IngestRequest, state: IngestState, turn: IngestTurn) -> str:
    """Paso 4: el turno a la sesión que corresponda."""
    await _step(request, "deliver", lambda ingest: ingest.deliver(request, state, turn))
    _ingested(request)
    return "delivered"


@activity.defn(name="describe_inbound_image")
@with_heartbeat(every=10)
async def describe_inbound_image_activity(request: IngestRequest) -> IngestRequest | None:
    """La foto: la guarda para el panel, la describe y devuelve su reentrada
    como texto (`<wamid>_vision`)."""
    reentry = await _step(request, "describe", lambda ingest: ingest.describe_image(request))
    if reentry is None:
        _ingested(request)
    return reentry


@activity.defn(name="transcribe_inbound_audio")
@with_heartbeat(every=10)
async def transcribe_inbound_audio_activity(request: IngestRequest) -> IngestRequest | None:
    """La nota de voz: la transcribe y devuelve su reentrada como texto
    (`<wamid>_transcribed`), o None si no se entendió (el cliente ya recibió
    el pedido de que lo escriba)."""
    reentry = await _step(request, "transcribe", lambda ingest: ingest.transcribe_audio(request))
    if reentry is None:
        _ingested(request)
    return reentry


@activity.defn(name="notify_inbound_photo_done")
async def notify_inbound_photo_done_activity(request: IngestRequest) -> str:
    """Le avisa a la sesión de ventas que la foto ya entró (o que su lectura
    falló): la ráfaga deja de esperarla. Nunca lanza."""
    from src.plugins.chats.agent.sales import composition

    await composition.build_ingest_use_case().photo_done(request)
    return "notified"


# --- El orden de entrega por cliente (ADR 2026-10-16) -----------------------

#: Tope de la espera al workflow del mensaje anterior antes de escribir el
#: historial: un mensaje normal termina en segundos (Jev hasta 10 s; una foto,
#: su visión más eso). Si el anterior se traba, este sale igual.
PREDECESSOR_GATE_S = 30


@activity.defn(name="register_inbound_arrival")
async def register_inbound_arrival_activity(session_id: str, arrival: InboundArrival) -> str:
    """Anuncia el mensaje al `InboundSessionWorkflow` de su cliente
    (`signal_with_start`: lo arranca si no está vivo). El workflow le responde
    con su anterior por la señal `predecessor`."""
    await activity.client().start_workflow(
        "InboundSessionWorkflow",
        InboundSessionState(),
        id=inbound_session_workflow_id(session_id),
        task_queue=activity.info().task_queue,
        start_signal="arrived",
        start_signal_args=[arrival],
    )
    return "registered"


@activity.defn(name="wait_inbound_workflow")
@with_heartbeat(every=10)
async def wait_inbound_workflow_activity(workflow_id: str) -> str:
    """Espera a que cierre el workflow de entrada `workflow_id` (el del mensaje
    anterior del cliente), a lo sumo `PREDECESSOR_GATE_S`. Mismos desenlaces
    que `wait_inbound_predecessor`: el orden no vale un mensaje perdido."""
    handle = activity.client().get_workflow_handle(workflow_id)
    try:
        await asyncio.wait_for(handle.result(), timeout=PREDECESSOR_GATE_S)
    except WorkflowFailureError:
        return "failed"
    except TimeoutError:
        return "timeout"
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return "not_found"
        raise
    return "done"
