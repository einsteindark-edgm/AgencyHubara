"""Las lecturas que describen al ÚLTIMO mensaje del cliente se escriben en el
orden de llegada (entrada durable, fase 2; revisión del PR).

Con el ingest en pasos, el paso del estado (que lee con Jev) corre en seguida
y en paralelo; el historial y la entrega van en orden. Tres lecturas describen
al último mensaje y no a la conversación:

* la señal de compra (`SIGNAL_KEY`: «luego te escribo», «sí»), que solo vale
  si es del `last_inbound_message_id`;
* la pausa por aplazamiento (una fecha; «cualquier otro texto» la levanta);
* la marca de cortesía.

Caso: el cliente manda una foto y, detrás, «gracias, luego te escribo el
lunes». El texto lee primero; la foto reentra después, cuando la visión
termina. Si la reentrada escribiera sus lecturas en su paso del estado,
borraría las del texto (que llegó DESPUÉS): el turno salía sin la nota de
aplazamiento, `closing_blocker` perdía `customer_deferred` y la pausa se
levantaba. Se escriben en el paso del historial, junto con
`last_inbound_message_id`, que va en orden.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any

import pytest

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.plugins.chats.agent.sales.contracts import IngestRequest
from src.plugins.chats.agent.sales.decisions.readings import COURTESY_KEY, Readings, last_inbound_is_courtesy
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.shared.purchase_signals import current_signal
from src.sdk.messagingkit import DEFERRAL_KEY, ReengagementDeferral
from tests.metadata_store_fakes import MergingMetadataStoreMixin

_SID = "wa_573001234567"
_NOW = 1_791_000_000_000
_MONDAY = _NOW + 3 * 86_400_000
_TEXT = "gracias, luego te escribo el lunes"


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {
            _SID: {"active_route": "ventas", "episodes": [{"episode_id": "ep_1", "started_at_ms": _NOW - 60_000}]}
        }

    def read(self, session_id: str) -> dict:
        return copy.deepcopy(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = copy.deepcopy(data)


class _History:
    def append_user_event(self, *_: Any, **__: Any) -> None:
        pass


class _Bot:
    def __init__(self) -> None:
        self.notes: list[list[str]] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None, **kw: Any) -> None:
        self.notes.append(list(kw.get("extra_context") or []))


class _Readings:
    """El texto aplaza (con fecha) y agradece; la foto no dice nada."""

    async def read(self, inbound: Any) -> Readings:
        if inbound.text == _TEXT:
            return Readings(
                purchase=("deferral", "text"),
                deferral=ReengagementDeferral(until_ms=_MONDAY, kind="fecha"),
                courtesy=True,
                opt_out=False,
                courtesy_only=True,
            )
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)

    async def read_ack(self, inbound: Any) -> Any:
        raise RuntimeError("sin acuse en este caso")


def _message(wamid: str, text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=wamid, from_number=_SID.removeprefix("wa_"), phone_number_id="PID", text=text, media=None,
        timestamp=str((_NOW - 5_000) // 1000),
    )


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_mod, "_now_ms", lambda: _NOW)


@pytest.mark.asyncio
async def test_a_photo_that_reenters_late_does_not_erase_the_readings_of_the_text_written_after_it() -> None:
    metadata, bot = _Metadata(), _Bot()
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=bot,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        readings=_Readings(),
    )
    text = IngestRequest(message=asdict(_message("wamid.T", _TEXT)), received_at_ms=_NOW - 1_000)
    photo = IngestRequest(
        message=asdict(_message("wamid.P_vision", "[el cliente envió una foto: una vela]")),
        received_at_ms=_NOW - 2_000,
        customer_text=None,
        customer_text_from_message=False,
        from_photo=True,
    )

    # El texto lee en seguida; la foto reentra cuando termina la visión.
    text_state = await ingest.read_state(text)
    photo_state = await ingest.read_state(photo)
    # El historial y la entrega van en orden de llegada: primero la foto.
    photo_turn = await ingest.prepare_turn(photo, photo_state)
    await ingest.write_history(photo, photo_state, photo_turn)
    await ingest.deliver(photo, photo_state, photo_turn)
    text_turn = await ingest.prepare_turn(text, text_state)
    await ingest.write_history(text, text_state, text_turn)
    await ingest.deliver(text, text_state, text_turn)

    doc = metadata.store[_SID]
    assert doc["last_inbound_message_id"] == "wamid.T"
    assert (current_signal(doc) or {}).get("kind") == "deferral"
    assert (doc.get(DEFERRAL_KEY) or {}).get("until_ms") == _MONDAY
    assert last_inbound_is_courtesy(doc) and doc[COURTESY_KEY]["message_id"] == "wamid.T"
    assert any("EL CLIENTE APLAZÓ" in note for note in bot.notes[-1])
