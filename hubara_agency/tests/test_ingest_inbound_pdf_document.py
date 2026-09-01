"""Inbound de documentos PDF (comprobantes de pago) — `IngestInboundMessage`.

Hoy un `type=document` entra como marker genérico "[el cliente envió un
document]" y los bytes se descartan: el operador no puede VER el comprobante
que el cliente mandó en PDF (las apps bancarias exportan PDF, no foto). Estos
tests exigen:

  * el PDF se descarga de Meta y se persiste en el media store (best-effort,
    como las imágenes) → el evento user del JSONL referencia `document_url` +
    `document_filename` para que el dashboard pinte un chip clickeable.
  * el marker que ve el LLM incluye el nombre del archivo (y el caption si
    vino), para que el agente pueda reaccionar con contexto.
  * documentos NO-PDF conservan el comportamiento actual (marker genérico,
    sin descarga).
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)

_PDF_BYTES = b"%PDF-1.4 comprobante"


# --- Fakes (espejo de test_ingest_inbound_message.py) ----------------------


@dataclass
class _Event:
    session_id: str
    content: str
    image_url: str | None
    document_url: str | None
    document_filename: str | None


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[_Event] = []

    def append_user_event(
        self,
        session_id: str,
        content: str,
        *,
        image_url: str | None = None,
        document_url: str | None = None,
        document_filename: str | None = None,
    ) -> None:
        self.events.append(
            _Event(session_id, content, image_url, document_url, document_filename)
        )


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
        extra_context: list[str] | None = None,
    ) -> None:
        self.messages.append(message)


class FakeMetadataStore:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)

    def update(self, session_id: str, mutator):
        fresh = self.read(session_id)
        result = mutator(fresh)
        if result is None:
            return None
        self.write(session_id, result)
        return result


def _make_document_message(
    *,
    mime: str = "application/pdf",
    filename: str | None = "comprobante.pdf",
    caption: str | None = None,
) -> WhatsAppMessage:
    media: dict = {"type": "document", "id": "doc-9", "mime_type": mime}
    if filename is not None:
        media["filename"] = filename
    if caption is not None:
        media["caption"] = caption
    return WhatsAppMessage(
        message_id="wamid.DOC",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=media,
        timestamp="1714312345",
    )


def _use_case(history, loader, store: FakeMetadataStore | None = None):
    return IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store or FakeMetadataStore(),  # type: ignore[arg-type]
    )


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_document_is_persisted_and_referenced_in_history(tmp_path):
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(return_value=(_PDF_BYTES, "application/pdf")),
        ),
    ):
        await _use_case(history, loader).execute(_make_document_message())

    assert len(history.events) == 1
    ev = history.events[0]
    assert ev.session_id == "wa_5491111111111"
    # El marker que ve el LLM nombra el archivo — no un "document" opaco.
    assert ev.content == "[el cliente envió un documento PDF: comprobante.pdf]"
    assert loader.messages == [ev.content]
    # Referencia servible + nombre visible para el chip del dashboard.
    assert ev.document_url is not None
    assert ev.document_url.startswith("/api/dashboard/media/wa_5491111111111/")
    assert ev.document_url.endswith(".pdf")
    assert ev.document_filename == "comprobante.pdf"
    # Y los bytes quedaron en el vault, servibles por GET /media.
    filename = ev.document_url.split("/")[-1]
    stored = tmp_path / "wa_5491111111111" / "media" / filename
    assert stored.read_bytes() == _PDF_BYTES


@pytest.mark.asyncio
async def test_pdf_document_caption_reaches_the_marker(tmp_path):
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(return_value=(_PDF_BYTES, "application/pdf")),
        ),
    ):
        await _use_case(history, loader).execute(
            _make_document_message(caption="ya pagué")
        )

    assert history.events[0].content == (
        '[el cliente envió un documento PDF: comprobante.pdf] con el texto: "ya pagué"'
    )


@pytest.mark.asyncio
async def test_pdf_document_without_filename_uses_generic_marker(tmp_path):
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(return_value=(_PDF_BYTES, "application/pdf")),
        ),
    ):
        await _use_case(history, loader).execute(
            _make_document_message(filename=None)
        )

    assert history.events[0].content == "[el cliente envió un documento PDF]"
    assert history.events[0].document_url is not None


@pytest.mark.asyncio
async def test_pdf_fetch_failure_still_surfaces_marker_without_url(tmp_path):
    """Best-effort como las imágenes: si Meta no responde, el marker igual
    llega al historial/LLM — solo que sin chip clickeable."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(side_effect=RuntimeError("meta down")),
        ),
    ):
        await _use_case(history, loader).execute(_make_document_message())

    assert len(history.events) == 1
    ev = history.events[0]
    assert ev.content == "[el cliente envió un documento PDF: comprobante.pdf]"
    assert ev.document_url is None
    assert loader.messages == [ev.content]


@pytest.mark.asyncio
async def test_non_pdf_document_keeps_generic_marker_and_no_fetch(tmp_path):
    """Regresión: un .docx (u otro mime) sigue el path actual — marker
    genérico, sin descarga de bytes."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    fetch_mock = AsyncMock()
    with patch(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes", new=fetch_mock
    ):
        await _use_case(history, loader).execute(
            _make_document_message(mime="application/msword", filename="doc.docx")
        )

    assert history.events[0].content == "[el cliente envió un document]"
    assert history.events[0].document_url is None
    fetch_mock.assert_not_awaited()


# --- Ruteo a humano + persistencia de índice (premortem PM-03/PM-04) --------

_SESSION = "wa_5491111111111"


def _fetch_ok():
    return AsyncMock(return_value=(_PDF_BYTES, "application/pdf"))


@pytest.mark.asyncio
async def test_pdf_document_routes_to_human_and_notifies_client(tmp_path):
    """Espejo determinista del comprobante-imagen (sin visión): un PDF del
    cliente va a verificación humana ANTES de que el bot improvise, y el
    cliente recibe el aviso de cortesía."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    store = FakeMetadataStore()
    send_mock = AsyncMock()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=_fetch_ok(),
        ),
        patch("src.platform.whatsapp.client.send_message", new=send_mock),
    ):
        await _use_case(history, loader, store).execute(_make_document_message())

    meta = store.store[_SESSION]
    assert meta["active_route"] == "humano"
    assert meta["tag"] == "HUMANO"
    assert meta["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"
    assert "comprobante.pdf" in meta["motivo"]
    assert meta["status_history"][-1]["active_route"] == "humano"
    # Aviso de cortesía al cliente (best-effort, mismo patrón que visión).
    send_mock.assert_awaited_once()
    args = send_mock.await_args.args
    assert args[0] == "PID"
    assert args[1] == "5491111111111"
    assert "Recibí tu documento" in args[2]


@pytest.mark.asyncio
async def test_pdf_document_already_humano_does_not_reroute_nor_notify(tmp_path):
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    store = FakeMetadataStore()
    store.store[_SESSION] = {"active_route": "humano", "tag": "HUMANO"}
    send_mock = AsyncMock()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=_fetch_ok(),
        ),
        patch("src.platform.whatsapp.client.send_message", new=send_mock),
    ):
        await _use_case(history, loader, store).execute(_make_document_message())

    send_mock.assert_not_awaited()
    assert store.store[_SESSION].get("status_history", []) == []
    # El PDF igual quedó servible para el humano que ya interviene.
    assert history.events[0].document_url is not None


@pytest.mark.asyncio
async def test_pdf_document_routes_to_human_even_if_fetch_fails(tmp_path):
    """Fail-toward-human: aunque no podamos persistir el PDF, un documento del
    cliente amerita verificación humana igual."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    store = FakeMetadataStore()
    send_mock = AsyncMock()

    with (
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(side_effect=RuntimeError("meta down")),
        ),
        patch("src.platform.whatsapp.client.send_message", new=send_mock),
    ):
        await _use_case(history, loader, store).execute(_make_document_message())

    assert store.store[_SESSION]["active_route"] == "humano"
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_pdf_document_persisted_is_indexed_for_retention(tmp_path):
    """PM-04: el PDF entra al `media_index` (Fase 0) con retención de
    comprobante — sin esto el futuro S3 Lifecycle nunca lo ve."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    store = FakeMetadataStore()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=_fetch_ok(),
        ),
        patch("src.platform.whatsapp.client.send_message", new=AsyncMock()),
    ):
        await _use_case(history, loader, store).execute(_make_document_message())

    index = store.store[_SESSION]["media_index"]
    assert len(index) == 1
    assert index[0]["media_id"] == "doc-9"
    assert index[0]["kind"] == "pdf_document"
    assert index[0]["retention_class"] == "receipt"
    assert index[0]["filename"].endswith(".pdf")


@pytest.mark.asyncio
async def test_pdf_document_spoofed_bytes_not_persisted_but_still_routed(tmp_path):
    """PM-10: mime de Meta dice PDF pero los bytes no abren con %PDF- → no se
    persiste (nada raro servible desde el vault), pero el humano igual toma."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    store = FakeMetadataStore()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=AsyncMock(return_value=(b"GIF89a-no-pdf", "application/pdf")),
        ),
        patch("src.platform.whatsapp.client.send_message", new=AsyncMock()),
    ):
        await _use_case(history, loader, store).execute(_make_document_message())

    assert history.events[0].document_url is None
    assert store.store[_SESSION]["active_route"] == "humano"
    assert not (tmp_path / _SESSION / "media").exists()


@pytest.mark.asyncio
async def test_pdf_fetch_is_capped_at_10mb(tmp_path):
    """PM-02: el fetch viaja con `max_bytes=10MB` — por encima, el fetcher NO
    descarga (la validación de tamaño es nuestra; el resto lo capea WhatsApp)."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    fetch_mock = _fetch_ok()

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=fetch_mock,
        ),
        patch("src.platform.whatsapp.client.send_message", new=AsyncMock()),
    ):
        await _use_case(history, loader).execute(_make_document_message())

    assert fetch_mock.await_args.kwargs.get("max_bytes") == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_pdf_filename_is_truncated_in_marker(tmp_path):
    """PM-06: un filename de 200 chars no infla el prompt ni el chip — se
    trunca a 80 en el marker y en el campo persistido."""
    import src.platform.media.store as media_store

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    long_name = "a" * 150 + ".pdf"

    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
            new=_fetch_ok(),
        ),
        patch("src.platform.whatsapp.client.send_message", new=AsyncMock()),
    ):
        await _use_case(history, loader).execute(
            _make_document_message(filename=long_name)
        )

    ev = history.events[0]
    assert ev.content == f"[el cliente envió un documento PDF: {'a' * 80}]"
    assert ev.document_filename is not None
    assert len(ev.document_filename) <= 80
