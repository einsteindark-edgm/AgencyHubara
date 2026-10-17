"""El ingest corre en una activity con reintentos (ADR 2026-10-15 §6): si el
worker muere a mitad, el mismo mensaje se ingiere otra vez. Ese reintento no
puede duplicar la línea del cliente en el panel ni hacer que el bot conteste
dos veces: el historial y el mensaje a la sesión salen una vez por `wamid`."""
from __future__ import annotations

from typing import Any

import pytest

from src.plugins.chats.agent.sales.inbound_effects_store import FilesystemInboundEffects
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin


class _History:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        self.events.append((session_id, content))


class _Session:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None, **_: Any) -> None:
        self.messages.append(message)


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)

    def update(self, session_id: str, mutator):
        result = mutator(self.read(session_id))
        if result is not None:
            self.write(session_id, result)
        return result


def _message(wamid: str = "wamid.A", text: str = "hola") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=wamid, from_number="573001234567", phone_number_id="PID", text=text, media=None, timestamp="1714312345"
    )


@pytest.fixture
def ingest(tmp_path):
    history, session = _History(), _Session()
    effects = FilesystemInboundEffects(tmp_path / "effects", now_ms=lambda: 1_789_992_000_000)
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=session,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        effects=effects,
    )
    return use_case, history, session, effects


@pytest.mark.asyncio
async def test_a_retried_message_reaches_the_panel_and_the_bot_once(ingest) -> None:
    use_case, history, session, _ = ingest

    await use_case.execute(_message())
    await use_case.execute(_message())

    assert history.events == [("wa_573001234567", "hola")]
    assert session.messages == ["hola"]


@pytest.mark.asyncio
async def test_a_retry_after_dying_between_history_and_bot_still_reaches_the_bot(ingest) -> None:
    use_case, history, session, effects = ingest
    effects.mark("wamid.A", "history")

    await use_case.execute(_message())

    assert history.events == []
    assert session.messages == ["hola"]


class _Transcriber:
    async def transcribe(self, request: Any) -> Any:
        from src.platform.audio.dtos import TranscriptionResult

        return TranscriptionResult(text="", ok=False, error="empty")


@pytest.mark.asyncio
async def test_a_courtesy_reply_to_the_customer_goes_out_once_per_message(ingest, monkeypatch) -> None:
    """La nota de voz que no se entendió: el reintento la vuelve a transcribir
    (falla igual) y NO le vuelve a pedir al cliente que escriba."""
    from src.platform.audio import composition
    from src.platform.whatsapp import client as wa_client

    sent: list[str] = []

    async def _send(_phone_id: str, _to: str, text: str, *_a: Any, **_k: Any) -> None:
        sent.append(text)

    monkeypatch.setattr(composition, "get_audio_transcription_port", lambda: _Transcriber())
    monkeypatch.setattr(wa_client, "send_message", _send)
    use_case, *_ = ingest
    voice = WhatsAppMessage(
        message_id="wamid.AUDIO", from_number="573001234567", phone_number_id="PID", text=None, media=None,
        audio={"id": "aud_1", "mime_type": "audio/ogg", "voice": True}, timestamp="1714312345", msg_type="audio",
    )

    await use_case._transcribe_and_reenter(voice)
    await use_case._transcribe_and_reenter(voice)

    assert len(sent) == 1
    assert "Recibí tu audio" in sent[0]


@pytest.mark.asyncio
async def test_two_different_messages_both_go_through(ingest) -> None:
    use_case, history, session, _ = ingest

    await use_case.execute(_message("wamid.A", "hola"))
    await use_case.execute(_message("wamid.B", "quiero velas"))

    assert [text for _, text in history.events] == ["hola", "quiero velas"]
    assert session.messages == ["hola", "quiero velas"]


def test_the_real_ingest_journals_its_effects_in_the_vault(monkeypatch, _isolate_vault_dir) -> None:
    """Gotcha #1 (tests verdes, feature muerta): el caso de uso que corre la
    activity (`build_ingest_use_case`) trae el diario, y el diario vive en el
    vault (sobrevive al worker que muere)."""
    import src.plugins.chats.agent.sales.composition as comp

    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)
    use_case = comp.build_ingest_use_case()

    use_case._mark_effect(_message("wamid.REAL"), "turn")

    assert list((_isolate_vault_dir / "_ledger" / "inbound_effects").rglob("*"))
    assert use_case._effect_done(_message("wamid.REAL"), "turn") is True


@pytest.mark.asyncio
async def test_the_document_ack_goes_out_once_even_if_the_retry_runs_in_a_new_process(tmp_path, monkeypatch) -> None:
    """Premortem PM-013 (spec «Un reintento del ingest no repite efectos»): el
    reintento corre en otro worker (proceso nuevo, metadata que el intento
    anterior no alcanzó a escribir): «Recibí tu documento» no sale dos veces."""
    from unittest.mock import AsyncMock

    import src.platform.media.store as media_store
    from src.platform.whatsapp import client as wa_client

    monkeypatch.setattr(media_store, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
        AsyncMock(return_value=(b"%PDF-1.4 comprobante", "application/pdf")),
    )
    sent: list[str] = []

    async def _send(_phone_id: str, _to: str, text: str, *_a: Any, **_k: Any) -> None:
        sent.append(text)

    monkeypatch.setattr(wa_client, "send_message", _send)
    document = WhatsAppMessage(
        message_id="wamid.DOC", from_number="573001234567", phone_number_id="PID", text=None,
        media={"type": "document", "id": "doc-9", "mime_type": "application/pdf", "filename": "comprobante.pdf"},
        timestamp="1714312345",
    )
    effects_root = tmp_path / "effects"

    for _attempt in range(2):
        use_case = IngestInboundMessage(
            history_store=_History(),  # type: ignore[arg-type]
            load_session=_Session(),  # type: ignore[arg-type]
            metadata_store=_Metadata(),  # type: ignore[arg-type]
            effects=FilesystemInboundEffects(effects_root, now_ms=lambda: 1_789_992_000_000),
        )
        await use_case.execute(document)

    assert len(sent) == 1
    assert "Recibí tu documento" in sent[0]
