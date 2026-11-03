"""ENV-03: the shipping form's data land in the order the moment they arrive.

Production gaps report (2026-11-03): the form reply (`nfm_reply`) arrives
structured (`city`, `address`, …), but it only reached the order draft if the
model called `set_order_slot` in that turn. When it did not (or its call was in
an attempt that got cut and recomposed), the draft could miss the data and the
bot could ask for them again. The data are facts, not a decision: the ingest
writes them into the draft of the active episode, like the web cart.

Not written: a conversation a human holds (the operator's draft reads the form
from the chat) and an episode whose order is already registered (the order is
the source of truth then).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

SID = "wa_570000000102"  # synthetic
PHONE = "570000000102"
# The ingest closes an idle episode by the wall clock: the episode is recent.
NOW_MS = int(time.time() * 1000)
STARTED_MS = NOW_MS - 10 * 60_000
FORM = {
    "city": "Bogotá",
    "neighborhood": "Chapinero",
    "address": "Calle 1 # 2-3",
    "phone": "3000000000",
    "receiver_name": "Ana Prueba",
    "national_id": "",
    "payment_method": "cash_on_delivery",
    "order_total_cop": "40000",
    "items_summary": "2× Vela",
    "flow_token": "shipping_wa_570000000102_1789406000",
}


class _History:
    def append_user_event(self, session_id: str, content: str, **_: object) -> None:
        return None


class _Loader:
    async def execute(self, session_id: str, message: str, phone_number_id: str | None,
                      extra_context: list[str] | None = None, inbound_meta: dict | None = None) -> None:
        return None


class _Store(MergingMetadataStoreMixin):
    def __init__(self, initial: dict[str, dict[str, Any]]) -> None:
        self.store = dict(initial)

    def read(self, session_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.store.get(session_id, {})))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.store[session_id] = json.loads(json.dumps(data))

    def update(self, session_id: str, mutator):
        fresh = self.read(session_id)
        result = mutator(fresh)
        if result is None:
            return None
        self.write(session_id, result)
        return result


def _store(**metadata: Any) -> _Store:
    episode = {
        "episode_id": "ep_001", "started_at_ms": STARTED_MS, "closed_at_ms": None,
        "order_draft": {"slots": {"producto": "Vela", "cantidad": "2"}, "updated_at_ms": STARTED_MS},
        **metadata.pop("episode", {}),
    }
    return _Store({SID: {"active_route": "ventas", "episodes": [episode], **metadata}})


async def _form_reply(store: _Store, response_json: dict[str, Any]) -> None:
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

    reply = {"type": "nfm_reply", "name": "flow", "body": "Sent", "response_json": response_json}
    use_case = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store)  # type: ignore[arg-type]
    await use_case.execute(WhatsAppMessage(message_id="wamid.form", from_number=PHONE, phone_number_id="PID",
                                           text="", media=None, timestamp=str(NOW_MS // 1000), interactive=reply))


def _slots(store: _Store) -> dict[str, Any]:
    return store.read(SID)["episodes"][-1]["order_draft"]["slots"]


@pytest.mark.asyncio
async def test_the_form_data_are_written_into_the_order_on_arrival(_isolate_vault_dir: Path) -> None:
    store = _store()

    await _form_reply(store, FORM)

    assert _slots(store) == {
        "producto": "Vela",
        "cantidad": "2",
        "ciudad": "Bogotá",
        "barrio": "Chapinero",
        "direccion": "Calle 1 # 2-3",
        "telefono": "3000000000",
        "nombre_recibe": "Ana Prueba",
        # The id the form sends is the one register_order takes.
        "metodo_pago": "cash_on_delivery",
    }


@pytest.mark.asyncio
async def test_a_field_left_blank_does_not_erase_what_the_order_had(_isolate_vault_dir: Path) -> None:
    store = _store(episode={"order_draft": {"slots": {"producto": "Vela", "cedula": "123"}, "updated_at_ms": 1}})

    await _form_reply(store, FORM)

    assert _slots(store)["cedula"] == "123"


@pytest.mark.asyncio
async def test_a_conversation_a_human_holds_is_left_to_the_operator(_isolate_vault_dir: Path) -> None:
    store = _store(active_route="humano", tag="HUMANO")

    await _form_reply(store, FORM)

    assert _slots(store) == {"producto": "Vela", "cantidad": "2"}


@pytest.mark.asyncio
async def test_an_episode_with_a_registered_order_is_not_touched(_isolate_vault_dir: Path) -> None:
    store = _store(episode={"order_id": "order_1"})

    await _form_reply(store, FORM)

    assert _slots(store) == {"producto": "Vela", "cantidad": "2"}
