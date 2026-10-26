"""El ingest le avisa a la sesión de ventas que una foto se está leyendo.

Texto ANTES de la foto (2026-09-30): el «¿tienes esta?» ya salió hacia el bot
cuando llega la foto; el ingest le avisa al workflow que hay una foto
leyéndose (la ráfaga la espera) y, cuando la foto ya entró, que terminó
(`tests/test_sales_burst_waits_for_photo.py`).

El texto DESPUÉS de la foto lo ordenaba `PhotoReads`, en memoria del proceso.
Desde la fase 2 de la entrada durable lo ordena `InboundSessionWorkflow`: cada
mensaje entrega después del anterior del mismo cliente
(`tests/plugins/chats/sales/test_inbound_session_order.py`, ADR 2026-10-16).
"""
from __future__ import annotations

from tests.metadata_store_fakes import MergingMetadataStoreMixin

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


class _Metadata(MergingMetadataStoreMixin):
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


def _ingest(loader: _Loader, **kw: Any) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        **kw,
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


# El texto ya salió hacia el bot cuando llega la foto: el ingest le avisa al
# workflow que hay una foto leyéndose (la ráfaga la espera) y, cuando la foto
# ya entró, que terminó (`tests/test_sales_burst_waits_for_photo.py`).

SESSION = f"wa_{CUSTOMER}"


async def test_the_bot_hears_that_a_photo_is_being_read_and_when_it_is_in(vision: _SlowVision) -> None:
    loader = _Loader()
    events: list[tuple[Any, ...]] = []

    async def notify(session_id: str, wamid: str, done: bool) -> None:
        events.append((session_id, wamid, done, list(loader.messages)))

    ingest = _ingest(loader, photo_notifier=notify)
    await ingest.execute(_text("¿tienes esta?"))
    photo = asyncio.create_task(ingest.execute(_photo("img_a")))
    await asyncio.sleep(0.05)
    announced = list(events)
    vision.finish("img_a")
    await asyncio.wait_for(photo, timeout=2)

    assert announced == [(SESSION, "wamid.img_a", False, ["¿tienes esta?"])]
    assert events[-1] == (SESSION, "wamid.img_a", True, ["¿tienes esta?", _photo_text("img_a")])


async def test_a_failing_notice_never_stops_the_photo(vision: _SlowVision) -> None:
    loader = _Loader()

    async def broken(session_id: str, wamid: str, done: bool) -> None:
        raise RuntimeError("temporal caído")

    ingest = _ingest(loader, photo_notifier=broken)
    vision.finish("img_a")
    await asyncio.wait_for(ingest.execute(_photo("img_a")), timeout=2)

    assert loader.messages == [_photo_text("img_a")]


def test_the_real_webhook_tells_the_sales_workflow_about_photos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gotcha #1 (tests verdes, feature muerta): el webhook real
    (`build_ingest_use_case`) avisa por el mismo caso de uso que le entrega los
    mensajes al workflow de ventas."""
    import src.plugins.chats.agent.sales.composition as comp

    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)
    use_case = comp.build_ingest_use_case()

    notifier = use_case._photo_notifier
    assert notifier is not None and notifier == use_case._load_session.notify_photo_reading
