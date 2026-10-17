"""La activity del ingest (ADR 2026-10-15): corre el `IngestInboundMessage` de
siempre con el mensaje tal como lo dejó el webhook (ya con la dirección de su
conversación) y deja el desenlace en el ledger de entrada — la verificación
cruzada: un `seen` sin `ingested` sigue queriendo decir «lo perdimos»."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales import composition
from src.plugins.chats.agent.sales.activities.inbound_entry import ingest_inbound_message_activity
from src.plugins.chats.agent.sales.contracts import InboundMessageInput
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage

_MESSAGE = WhatsAppMessage(
    message_id="wamid.A",
    from_number="CO9990000000000002",
    phone_number_id="PID",
    text="hola",
    media=None,
    timestamp="1789992000",
    profile_name="Ana",
    wa_user_id="CO.9990000000000002",
    raw={"id": "wamid.A", "type": "text"},
)


class _Ingest:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.received: list[WhatsAppMessage] = []
        self.kwargs: list[dict[str, Any]] = []

    async def execute(self, parsed: WhatsAppMessage, **kwargs: Any) -> None:
        self.received.append(parsed)
        self.kwargs.append(kwargs)
        if self.error is not None:
            raise self.error


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, records: list[dict[str, Any]]) -> None:
        self.records.extend(records)


@pytest.fixture
def ledger(monkeypatch) -> _Ledger:
    fake = _Ledger()
    monkeypatch.setattr(composition, "build_inbound_ledger", lambda: fake)
    return fake


async def _run(monkeypatch, ingest: _Ingest) -> str:
    monkeypatch.setattr(composition, "build_ingest_use_case", lambda: ingest)
    inp = InboundMessageInput(message=asdict(_MESSAGE), received_at_ms=1_789_992_000_000)
    return await ActivityEnvironment().run(ingest_inbound_message_activity, inp)


@pytest.mark.asyncio
async def test_the_ingest_receives_the_message_as_the_webhook_left_it(monkeypatch, ledger) -> None:
    ingest = _Ingest()

    result = await _run(monkeypatch, ingest)

    assert result == "ingested"
    assert ingest.received == [_MESSAGE]


@pytest.mark.asyncio
async def test_the_ingest_measures_lateness_from_the_webhook_arrival(monkeypatch, ledger) -> None:
    """Fase 2, paso 5: un intento que corre minutos después no vuelve tarde un
    mensaje que llegó a tiempo — el reloj es `received_at_ms`."""
    ingest = _Ingest()

    await _run(monkeypatch, ingest)

    assert ingest.kwargs == [{"received_at_ms": 1_789_992_000_000}]


@pytest.mark.asyncio
async def test_an_ingested_message_leaves_its_outcome_in_the_ledger(monkeypatch, ledger) -> None:
    await _run(monkeypatch, _Ingest())

    assert [(r["stage"], r["wa_message_id"], r["session_id"]) for r in ledger.records] == [
        ("ingested", "wamid.A", "wa_CO9990000000000002")
    ]


@pytest.mark.asyncio
async def test_a_failed_ingest_is_ledgered_and_raised_so_temporal_retries(monkeypatch, ledger) -> None:
    with pytest.raises(RuntimeError, match="vault roto"):
        await _run(monkeypatch, _Ingest(error=RuntimeError("vault roto")))

    assert [(r["stage"], r["error"]) for r in ledger.records] == [("ingest_failed", "RuntimeError: vault roto")]


class _Stuck(_Ingest):
    """Ingest que no termina: el worker se apaga a mitad (deploy) y Temporal
    cancela la activity."""

    def __init__(self) -> None:
        super().__init__()
        import asyncio

        self.started = asyncio.Event()

    async def execute(self, parsed: WhatsAppMessage, **kwargs: Any) -> None:
        import asyncio

        self.received.append(parsed)
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_a_cancelled_ingest_is_not_ledgered_as_ingested(monkeypatch, ledger) -> None:
    """Sin esto el `finally` dejaba `ingested` de un mensaje que no terminó:
    el ledger diría que llegó al bot cuando lo que pasó es que se reintenta."""
    import asyncio

    ingest = _Stuck()
    monkeypatch.setattr(composition, "build_ingest_use_case", lambda: ingest)
    env = ActivityEnvironment()
    run = asyncio.create_task(
        env.run(ingest_inbound_message_activity, InboundMessageInput(message=asdict(_MESSAGE)))
    )
    await ingest.started.wait()

    env.cancel()
    with pytest.raises(BaseException):
        await run

    assert [r["stage"] for r in ledger.records] == ["ingest_failed"]


@pytest.mark.asyncio
async def test_a_message_queued_by_another_version_still_ingests(monkeypatch, ledger) -> None:
    """Un mensaje que quedó en Temporal durante un deploy puede traer un campo
    que esta versión ya no conoce: se ignora, el mensaje entra igual."""
    ingest = _Ingest()
    monkeypatch.setattr(composition, "build_ingest_use_case", lambda: ingest)
    inp = InboundMessageInput(message={**asdict(_MESSAGE), "campo_de_otra_version": "x"})

    assert await ActivityEnvironment().run(ingest_inbound_message_activity, inp) == "ingested"
    assert ingest.received == [_MESSAGE]
