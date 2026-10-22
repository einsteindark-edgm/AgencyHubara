"""El ingest dentro de una activity se puede CANCELAR (el worker se apaga, un
heartbeat vencido, un reset desde la UI) y dos intentos del mismo mensaje se
pueden superponer. En la API (BackgroundTasks) nada de eso pasaba (premortem
del PR #443).

- PM-003: el aviso a la sesión sale una vez aunque dos intentos corran a la
  vez o el intento se cancele mientras avisa (sin esto el bot contestaba dos
  veces: la sesión V2 no deduplica por wamid).
- PM-006: una cancelación entre «empieza a leerse la foto» y lanzar la lectura
  no deja al cliente con una foto «leyéndose» para siempre (cada texto suyo
  esperaba 10 s hasta reiniciar el worker). Desde la fase 2 no hay
  `PhotoReads`: el texto espera a la foto en `InboundSessionWorkflow`, con
  tope (`test_inbound_session_order.py`), y aquí ya no espera nada.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from src.plugins.chats.agent.sales.inbound_effects_store import FilesystemInboundEffects
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

CUSTOMER = "573001234567"


class _History:
    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        pass


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def read(self, session_id: str) -> dict[str, Any]:
        return dict(self.data.get(session_id, {}))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.data[session_id] = dict(data)

    def update(self, session_id: str, mutator: Any) -> dict[str, Any] | None:
        out = mutator(self.read(session_id))
        if out is not None:
            self.write(session_id, out)
        return out


class _Session:
    """La sesión de ventas: el aviso tarda (un signal_with_start a Temporal
    Cloud) y, si `hold`, no termina hasta que el test lo suelta."""

    def __init__(self, *, hold: bool = False) -> None:
        self.messages: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def execute(self, *, message: str, **_: Any) -> None:
        self.entered.set()
        await asyncio.sleep(0.05)
        await self.release.wait()
        self.messages.append(message)


def _ingest(tmp_path, session: _Session, **kw: Any) -> tuple[IngestInboundMessage, FilesystemInboundEffects]:
    effects = FilesystemInboundEffects(tmp_path / "effects", now_ms=lambda: 1_789_992_000_000)
    use_case = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=session,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        effects=effects,
        **kw,
    )
    return use_case, effects


def _text(text: str = "hola", wamid: str = "wamid.A") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=wamid, from_number=CUSTOMER, phone_number_id="PID", text=text, media=None, timestamp="1714312346"
    )


def _photo(media_id: str = "img1") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=f"wamid.{media_id}", from_number=CUSTOMER, phone_number_id="PID", text=None,
        media={"type": "image", "id": media_id, "mime_type": "image/jpeg"}, timestamp="1714312345",
        msg_type="image",
    )


@pytest.mark.asyncio
async def test_two_overlapping_attempts_of_the_same_message_reach_the_bot_once(tmp_path) -> None:
    session = _Session()
    use_case, _ = _ingest(tmp_path, session)

    await asyncio.gather(use_case.execute(_text()), use_case.execute(_text()))

    assert session.messages == ["hola"]


@pytest.mark.asyncio
async def test_an_attempt_cancelled_while_telling_the_bot_still_leaves_its_mark(tmp_path) -> None:
    session = _Session(hold=True)
    use_case, effects = _ingest(tmp_path, session)
    attempt = asyncio.create_task(use_case.execute(_text()))
    await session.entered.wait()

    attempt.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await attempt
    session.release.set()
    await asyncio.sleep(0.2)

    assert session.messages == ["hola"]
    assert effects.done("wamid.A", "turn") is True
    await use_case.execute(_text())  # el reintento de Temporal
    assert session.messages == ["hola"]


@pytest.mark.asyncio
async def test_a_photo_cancelled_before_its_reading_starts_does_not_hold_the_customers_texts(tmp_path) -> None:
    notice = asyncio.Event()

    async def stuck_notifier(session_id: str, wamid: str, done: bool) -> None:
        notice.set()
        await asyncio.Event().wait()

    session = _Session()
    use_case, _ = _ingest(tmp_path, session, photo_notifier=stuck_notifier)
    attempt = asyncio.create_task(use_case.execute(_photo()))
    await notice.wait()

    attempt.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await attempt

    await asyncio.wait_for(use_case.execute(_text("¿tienes esta?", wamid="wamid.T")), timeout=2)
    assert session.messages == ["¿tienes esta?"]
