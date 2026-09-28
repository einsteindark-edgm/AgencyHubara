"""El ingest pide las lecturas del cliente al PROVEEDOR inyectado (motor de
decisiones F2, enchufe 1) y escribe los mismos campos de hoy.

* Sin proveedor explícito, el del motor (con `reglas` por defecto: el turno
  es el de hoy).
* El proveedor recibe lo que escribió el cliente, el metadata ANTES de este
  mensaje y el historial que el cliente vio.
* Lo que escribe la visión (la descripción de una foto) no es texto del
  cliente: la lectura recibe solo el texto que el cliente puso en la foto.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

SID = "wa_573001234567"


class _History:
    def __init__(self, events: list[dict] | None = None) -> None:
        self._events = list(events or [])

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self._events.append({"role": "user", "content": content})

    def read_events(self, session_id: str) -> list[dict]:
        return list(self._events)


class _Loader:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class _Store:
    def __init__(self, seed: dict[str, dict] | None = None) -> None:
        self.store: dict[str, dict] = dict(seed or {})

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


class _Provider:
    def __init__(self, readings: Readings) -> None:
        self.readings = readings
        self.seen: list[Inbound] = []

    async def read(self, inbound: Inbound) -> Readings:
        self.seen.append(inbound)
        return self.readings


def _msg(text: str, message_id: str = "wamid.X") -> WhatsAppMessage:
    return WhatsAppMessage(message_id=message_id, from_number="573001234567", phone_number_id="PID", text=text, media=None,
                           timestamp=str(int(time.time())))


def _draft_store(**extra) -> _Store:
    return _Store({SID: {"episodes": [{"episode_id": "ep_1", "started_at_ms": int(time.time() * 1000) - 5000,
                                       "closed_at_ms": None, "order_draft": {"slots": {"producto": "Cubo Love"}}}], **extra}})


@pytest.mark.asyncio
async def test_the_ingest_writes_what_the_provider_read(_isolate_vault_dir) -> None:
    store = _draft_store()
    provider = _Provider(Readings(purchase=("affirmation", "text"), deferral=None, courtesy=False, opt_out=False))
    history = _History([{"role": "assistant", "content": "¿Confirmas el pedido?"}])
    ingest = IngestInboundMessage(history_store=history, load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]

    await ingest.execute(_msg("Te confirmo, sí la quiero"))

    md = store.read(SID)
    assert md["last_inbound_signal"]["kind"] == "affirmation"
    assert isinstance(md["episodes"][0]["order_draft"].get("confirmed_at_ms"), int)
    [inbound] = provider.seen
    assert inbound.text == "Te confirmo, sí la quiero" and inbound.message_id == "wamid.X"
    assert inbound.events == [{"role": "assistant", "content": "¿Confirmas el pedido?"}]  # antes de este mensaje
    assert inbound.stage == "etapa_variantes"


@pytest.mark.asyncio
async def test_the_ingest_writes_the_opt_out_the_provider_read(_isolate_vault_dir) -> None:
    now_ms = int(time.time() * 1000)
    store = _draft_store(campaign_touches=[{"campaign_id": "mkt-1", "sent_at_ms": now_ms - 60_000}])
    provider = _Provider(Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=True))
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]

    await ingest.execute(_msg("por favor no me contacten"))

    assert store.read(SID)["marketing_opt_out"] is True


@pytest.mark.asyncio
async def test_without_a_provider_the_readings_are_todays(_isolate_vault_dir) -> None:
    store = _draft_store()
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store)  # type: ignore[arg-type]

    await ingest.execute(_msg("sí, dale"))

    assert store.read(SID)["last_inbound_signal"]["kind"] == "affirmation"


@pytest.mark.asyncio
async def test_the_vision_description_is_not_read_as_the_customer(_isolate_vault_dir) -> None:
    store = _draft_store()
    provider = _Provider(Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False))
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]
    receipt = '[el cliente envió una foto: comprobante de pago del viernes 25 de septiembre] con el texto: "listo, ya pagué"'

    await ingest.execute(_msg(receipt, "wamid.X_vision"), persisted_image_url="media/x.jpg", customer_text="listo, ya pagué")
    await ingest.execute(_msg("[el cliente envió una foto: una vela roja]", "wamid.Y_vision"), persisted_image_url="media/y.jpg",
                         customer_text=None)

    assert [i.text for i in provider.seen] == ["listo, ya pagué", None]
