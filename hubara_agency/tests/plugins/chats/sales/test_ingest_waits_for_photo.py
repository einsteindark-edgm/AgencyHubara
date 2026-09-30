"""El texto que el cliente manda justo después de una foto espera a la foto.

La foto entra al bot cuando la visión termina de leerla (1,5 s si basta el
texto de la foto; hasta ~4,5 s si hay que compararla con las fotos del
catálogo). La ráfaga se cierra tras 1,5 s de silencio, así que el «¿tienes
esta?» que el cliente escribe detrás de la foto llegaba solo y el bot
contestaba sin la foto; la foto entraba después como otro turno (laboratorio
caso-fotos-0930-r7). Ahora el ingest retiene el texto mientras se lee una foto
del mismo cliente: la foto entra primero y el texto justo detrás, en la misma
ráfaga. Las fotos se siguen leyendo a la vez (no se esperan entre sí), otro
cliente no espera, y la espera tiene tope: si la visión se cuelga, el texto
sale igual.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.sdk.connectorkit import VISION_KIND_PRODUCT_PHOTO, VisionResult

CUSTOMER = "573001234567"


class _SlowVision:
    """Visión que no termina hasta que el test la suelta, foto por foto."""

    def __init__(self) -> None:
        self._done: dict[str, asyncio.Event] = {}

    def finish(self, media_id: str) -> None:
        self._done.setdefault(media_id, asyncio.Event()).set()

    async def describe(self, request: Any) -> VisionResult:
        await self._done.setdefault(request.media_id, asyncio.Event()).wait()
        return VisionResult(description=f"vela de la foto {request.media_id}", ok=True, kind=VISION_KIND_PRODUCT_PHOTO)


@pytest.fixture
def vision(monkeypatch: pytest.MonkeyPatch) -> _SlowVision:
    import src.platform.vision.composition as composition

    fake = _SlowVision()
    monkeypatch.setattr(composition, "get_image_vision_port", lambda: fake)
    return fake


class _History:
    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        pass


class _Loader:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def execute(self, **kw: Any) -> None:
        self.messages.append(kw["message"])


class _Metadata:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def read(self, session_id: str) -> dict[str, Any]:
        return dict(self.data.get(session_id, {}))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.data[session_id] = dict(data)

    def update(self, session_id: str, mutator: Any) -> dict[str, Any] | None:
        fresh = self.read(session_id)
        out = mutator(fresh)
        if out is not None:
            self.write(session_id, out)
        return out


def _ingest(loader: _Loader, *, photo_wait_s: float = 5.0) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        photo_wait_s=photo_wait_s,
    )


def _photo(media_id: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=f"wamid.{media_id}", from_number=CUSTOMER, phone_number_id="PID", text=None,
        media={"type": "image", "id": media_id, "mime_type": "image/jpeg"}, timestamp="1714312345",
        msg_type="image",
    )


def _text(text: str, *, sender: str = CUSTOMER) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=f"wamid.{abs(hash((text, sender)))}", from_number=sender, phone_number_id="PID", text=text,
        media=None, timestamp="1714312346", msg_type="text",
    )


def _photo_text(media_id: str) -> str:
    return f"[el cliente envió una foto: vela de la foto {media_id}]"


async def test_the_text_right_after_a_photo_reaches_the_bot_after_the_photo(vision: _SlowVision) -> None:
    loader = _Loader()
    ingest = _ingest(loader)

    await ingest.execute(_photo("img_a"))
    text = asyncio.create_task(ingest.execute(_text("¿tienes esta?")))
    await asyncio.sleep(0.05)
    held = list(loader.messages)
    vision.finish("img_a")
    await asyncio.wait_for(text, timeout=2)

    assert held == []
    assert loader.messages == [_photo_text("img_a"), "¿tienes esta?"]


async def test_photos_are_read_at_once_and_the_text_waits_for_all_of_them(vision: _SlowVision) -> None:
    loader = _Loader()
    ingest = _ingest(loader)

    await ingest.execute(_photo("img_a"))
    await ingest.execute(_photo("img_b"))
    text = asyncio.create_task(ingest.execute(_text("¿las tienes?")))
    vision.finish("img_b")
    await asyncio.sleep(0.05)
    while_a_is_read = list(loader.messages)
    vision.finish("img_a")
    await asyncio.wait_for(text, timeout=2)

    assert while_a_is_read == [_photo_text("img_b")]
    assert loader.messages == [_photo_text("img_b"), _photo_text("img_a"), "¿las tienes?"]


async def test_the_wait_has_a_limit(vision: _SlowVision) -> None:
    loader = _Loader()
    ingest = _ingest(loader, photo_wait_s=0.05)

    await ingest.execute(_photo("img_a"))
    await asyncio.wait_for(ingest.execute(_text("hola")), timeout=1)
    vision.finish("img_a")
    await asyncio.sleep(0.05)

    assert loader.messages == ["hola", _photo_text("img_a")]


async def test_another_customer_does_not_wait(vision: _SlowVision) -> None:
    loader = _Loader()
    ingest = _ingest(loader)

    await ingest.execute(_photo("img_a"))
    await asyncio.wait_for(ingest.execute(_text("hola", sender="573009999999")), timeout=1)

    assert loader.messages == ["hola"]
    vision.finish("img_a")
    await asyncio.sleep(0.05)
