"""Las activities de los pasos del ingest (entrada durable, fase 2).

Cada una corre un paso del `IngestInboundMessage` de siempre y deja el
desenlace del mensaje en el ledger de entrada (la verificación cruzada: un
`seen` sin `ingested` sigue queriendo decir «lo perdimos»):

* `ingest_failed` cuando un paso falla (Temporal reintenta ESE paso);
* `ingested` una vez, cuando el mensaje terminó su camino: entregado, solo al
  chat, sin nada que escribir, o una nota de voz que no se entendió. La
  reentrada de una foto (`<wamid>_vision`) cuenta como el mensaje del webhook.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales import composition
from src.plugins.chats.agent.sales.activities.inbound_entry import (
    deliver_inbound_turn_activity,
    prepare_inbound_turn_activity,
    read_inbound_state_activity,
    transcribe_inbound_audio_activity,
)
from src.plugins.chats.agent.sales.contracts import (
    TURN_DELIVER,
    TURN_IGNORED,
    IngestRequest,
    IngestState,
    IngestTurn,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage

_MESSAGE = WhatsAppMessage(
    message_id="wamid.A", from_number="573001234567", phone_number_id="PID", text="hola", media=None,
    timestamp="1789992000",
)
_REQUEST = IngestRequest(message=asdict(_MESSAGE), received_at_ms=1_789_992_000_000)
_STATE = IngestState(session_id="wa_573001234567", now_ms=1, sent_ms=1, arrival_ms=1)


class _Ingest:
    def __init__(self, *, state: IngestState = _STATE, turn: str = TURN_DELIVER, fail: str | None = None) -> None:
        self.state, self.turn, self.fail = state, turn, fail
        self.delivered: list[IngestRequest] = []

    def _maybe_fail(self, step: str) -> None:
        if self.fail == step:
            raise RuntimeError(f"{step} roto")

    async def read_state(self, request: IngestRequest) -> IngestState:
        self._maybe_fail("state")
        return self.state

    async def prepare_turn(self, request: IngestRequest, state: IngestState) -> IngestTurn:
        self._maybe_fail("turn")
        return IngestTurn(outcome=self.turn)

    async def deliver(self, request: IngestRequest, state: IngestState, turn: IngestTurn) -> None:
        self._maybe_fail("deliver")
        self.delivered.append(request)

    async def transcribe_audio(self, request: IngestRequest) -> IngestRequest | None:
        return None


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, records: list[dict[str, Any]]) -> None:
        self.records.extend(records)

    def stages(self) -> list[tuple[str, str]]:
        return [(r["stage"], r["wa_message_id"]) for r in self.records]


@pytest.fixture
def ledger(monkeypatch) -> _Ledger:
    fake = _Ledger()
    monkeypatch.setattr(composition, "build_inbound_ledger", lambda: fake)
    return fake


def _use(monkeypatch, ingest: _Ingest) -> None:
    monkeypatch.setattr(composition, "build_ingest_use_case", lambda: ingest)


@pytest.mark.asyncio
async def test_a_state_that_could_not_be_written_is_retried(monkeypatch, ledger) -> None:
    """El episodio y las lecturas no se pierden por un disco que falló al
    escribir: el paso lanza y Temporal lo reintenta."""
    _use(monkeypatch, _Ingest(state=replace(_STATE, write_failed=True)))

    with pytest.raises(ApplicationError):
        await ActivityEnvironment().run(read_inbound_state_activity, _REQUEST)


@pytest.mark.asyncio
async def test_a_failing_step_leaves_its_mark_in_the_ledger_and_raises(monkeypatch, ledger) -> None:
    _use(monkeypatch, _Ingest(fail="turn"))

    with pytest.raises(RuntimeError):
        await ActivityEnvironment().run(prepare_inbound_turn_activity, _REQUEST, _STATE)

    assert ledger.stages() == [("ingest_failed", "wamid.A")]
    assert "turn" in ledger.records[0]["error"]


@pytest.mark.asyncio
async def test_a_delivered_message_is_ingested_once(monkeypatch, ledger) -> None:
    ingest = _Ingest()
    _use(monkeypatch, ingest)

    await ActivityEnvironment().run(prepare_inbound_turn_activity, _REQUEST, _STATE)
    await ActivityEnvironment().run(deliver_inbound_turn_activity, _REQUEST, _STATE, IngestTurn(outcome=TURN_DELIVER))

    assert ledger.stages() == [("ingested", "wamid.A")]
    assert ingest.delivered == [_REQUEST]


@pytest.mark.asyncio
async def test_a_photo_that_reentered_counts_as_the_message_that_arrived(monkeypatch, ledger) -> None:
    _use(monkeypatch, _Ingest())
    reentry = replace(_REQUEST, message={**_REQUEST.message, "message_id": "wamid.A_vision"}, from_photo=True)

    await ActivityEnvironment().run(deliver_inbound_turn_activity, reentry, _STATE, IngestTurn(outcome=TURN_DELIVER))

    assert ledger.stages() == [("ingested", "wamid.A")]


@pytest.mark.asyncio
async def test_a_message_with_nothing_to_write_is_ingested_at_its_turn(monkeypatch, ledger) -> None:
    _use(monkeypatch, _Ingest(turn=TURN_IGNORED))

    await ActivityEnvironment().run(prepare_inbound_turn_activity, _REQUEST, _STATE)

    assert ledger.stages() == [("ingested", "wamid.A")]


@pytest.mark.asyncio
async def test_a_voice_note_nobody_understood_is_ingested_there(monkeypatch, ledger) -> None:
    _use(monkeypatch, _Ingest())

    assert await ActivityEnvironment().run(transcribe_inbound_audio_activity, _REQUEST) is None
    assert ledger.stages() == [("ingested", "wamid.A")]
