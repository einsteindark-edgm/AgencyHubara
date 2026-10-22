"""El ingest de Ventas marca el perfil «interesado sin compra» en vivo
(operador, 2026-10-20). Con Jev, lo lee la capacidad `compra` (eligió,
aplazó, dio datos, confirmó); aquí, sin Jev, la regla de hoy (el aplazamiento
del texto). Preguntar o saludar no marca nada.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.shared import customer_profiles as cp
from tests.test_ingest_inbound_message import FakeHistoryStore, FakeLoadOrStart, FakeMetadataStore


def _message(i: int, text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=f"wamid.PROF{i}", from_number="5490000000000", phone_number_id="PID",
        text=text, media=None, timestamp=str(1714312345 + 60 * i),
    )


async def _metadata_after(*texts: str) -> dict:
    store = FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )
    for i, text in enumerate(texts):
        await ingest.execute(_message(i, text))
    (sid,) = store.store
    return store.store[sid]


@pytest.mark.asyncio
async def test_a_customer_who_defers_the_purchase_gets_the_profile() -> None:
    metadata = await _metadata_after("Hola, ¿qué precio tienen las velas?", "Lo pienso y te aviso")

    mark = metadata[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]
    assert mark["source"] == "regla" and mark["episode_id"] == metadata["episodes"][-1]["episode_id"]


@pytest.mark.asyncio
async def test_asking_the_price_does_not_profile_anybody() -> None:
    metadata = await _metadata_after("Hola, ¿qué precio tienen las velas?")

    assert cp.PROFILE_KEY not in metadata


async def _after_a_change_to_the_episode(change: dict, text: str) -> dict:
    """Primer mensaje, luego `change` en el episodio activo (lo que hizo el
    turno del bot) y el mensaje `text`."""
    store = FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )
    await ingest.execute(_message(0, "Hola, ¿qué precio tienen las velas?"))
    (sid,) = store.store
    store.store[sid]["episodes"][-1].update(change)
    await ingest.execute(_message(1, text))
    return store.store[sid]


@pytest.mark.asyncio
async def test_a_half_made_order_profiles_the_customer_without_any_reading() -> None:
    draft = {"order_draft": {"items": [{"producto": "Caja de Velas de deseos cuadradas"}]}}

    metadata = await _after_a_change_to_the_episode(draft, "¿Y cuánto se demora el envío a Medellín?")

    assert metadata[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]["source"] == "pedido_a_medias"


@pytest.mark.asyncio
async def test_an_episode_with_its_order_is_not_profiled() -> None:
    metadata = await _after_a_change_to_the_episode({"order_id": "order_1"}, "Lo pienso y te aviso")

    assert cp.PROFILE_KEY not in metadata


# ── revisión del 2026-10-20: un humano tomó la conversación mientras se esperaba a Jev ─
#
# El mensaje abría un episodio nuevo (el anterior había cerrado) y la marca
# apuntaba a ese episodio; con la toma, el episodio se descarta (manda el
# humano) y la marca no puede quedar apuntando a un episodio que no existe.

SID = "wa_10000000001"
DAY = 86_400_000


class _OperatorTakesOverWhileWaitingForJev:
    def __init__(self, store: FilesystemMetadataStore) -> None:
        self._store = store

    async def read(self, inbound: Inbound) -> Readings:
        self._store.update(
            inbound.session_id,
            lambda fresh: {**fresh, "active_route": "humano", "tag": "HUMANO", "motivo": "Humano tomó el control"},
        )
        return Readings(purchase=("deferral", "text"), deferral=None, courtesy=False, opt_out=False)


class _NoHistory:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass

    def read_events(self, session_id: str) -> list[dict]:
        return []


class _NoLoader:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("earlier_mark", [None, {"first_at_ms": 1_000, "at_ms": 1_000, "episode_id": "ep_001",
                                                  "source": "jev", "campaign": None}])
async def test_a_human_takeover_during_the_wait_drops_the_mark_of_the_discarded_episode(
    _isolate_vault_dir, earlier_mark: dict | None
) -> None:
    store = FilesystemMetadataStore(_isolate_vault_dir)
    now_ms = int(time.time() * 1000)
    closed = {"episode_id": "ep_001", "started_at_ms": now_ms - 3 * DAY, "closed_at_ms": now_ms - 2 * DAY,
              "closing_tag": "SIN_RESPUESTA"}
    seed: dict[str, Any] = {"phone_number_id": "pnid-1", "active_route": "ventas", "tag": "SIN_RESPUESTA",
                            "episodes": [closed]}
    if earlier_mark is not None:
        seed[cp.PROFILE_KEY] = {cp.INTERESADO_SIN_COMPRA: earlier_mark}
    store.write(SID, seed)
    ingest = IngestInboundMessage(
        history_store=_NoHistory(),  # type: ignore[arg-type]
        load_session=_NoLoader(),  # type: ignore[arg-type]
        metadata_store=store,
        readings=_OperatorTakesOverWhileWaitingForJev(store),
    )

    await ingest.execute(WhatsAppMessage(
        message_id="wamid.in", from_number="10000000001", phone_number_id="pnid-1",
        text="Lo pienso y te aviso", media=None, timestamp=str(now_ms // 1000),
    ))

    metadata = store.read(SID)
    assert metadata["active_route"] == "humano" and metadata["episodes"] == [closed]
    assert metadata.get(cp.PROFILE_KEY) == seed.get(cp.PROFILE_KEY)
