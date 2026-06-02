"""Tests del pipeline de visión de imágenes inbound (ingest).

El agente (DeepSeek) es text-only. Cuando llega una imagen:
  * se difiere a la descripción por visión (Gemini) — NO se delega al agente
    hasta tener la descripción (igual que el audio);
  * la descripción se reinyecta como texto sintético;
  * si es un comprobante de pago y la conversación NO está en turno humano,
    se asigna al humano (verificación de pago) antes de pasarla al agente.

Usan el `_FakeVisionAdapter` (``IMAGE_VISION_PROVIDER=fake``) que clasifica por
marcador en el ``media_id`` — sin red.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)


# --- Fakes -----------------------------------------------------------------


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        # image_urls[i] acompaña a events[i] — None salvo imágenes persistidas.
        self.image_urls: list[str | None] = []

    def append_user_event(
        self, session_id: str, content: str, *, image_url: str | None = None
    ) -> None:
        self.events.append((session_id, content))
        self.image_urls.append(image_url)


@dataclass
class _Call:
    session_id: str
    message: str
    phone_number_id: str | None
    extra_context: list[str] | None = None


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
        extra_context: list[str] | None = None,
    ) -> None:
        self.calls.append(_Call(session_id, message, phone_number_id, extra_context))


class FakeMetadataStore:
    def __init__(self, seed: dict | None = None) -> None:
        self.store: dict[str, dict] = {}
        if seed:
            self.store["wa_5491111111111"] = dict(seed)

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


def _make_image(media_id: str, *, caption: str | None = None) -> WhatsAppMessage:
    media: dict = {"type": "image", "id": media_id, "mime_type": "image/jpeg"}
    if caption is not None:
        media["caption"] = caption
    return WhatsAppMessage(
        message_id="wamid.IMG",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=media,
        timestamp="1714312345",
        msg_type="image",
    )


def _make_use_case(
    history: FakeHistoryStore,
    loader: FakeLoadOrStart,
    metadata: FakeMetadataStore,
) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _reset_vision_cache():
    from src.platform.vision.composition import get_image_vision_port

    get_image_vision_port.cache_clear()
    yield
    get_image_vision_port.cache_clear()


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_inbound_defers_to_vision(monkeypatch):
    """Una imagen NO delega al agente de inmediato — queda `pending_vision`
    en metadata y se encola la descripción (igual que el audio)."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case.execute(_make_image("img_1"))

    # En el punto de diferir: nada al agente todavía, pending_vision seteado.
    assert history.events == []
    assert loader.calls == []
    assert metadata.store["wa_5491111111111"]["pending_vision"]["media_id"] == "img_1"


@pytest.mark.asyncio
async def test_describe_product_photo_reenters_as_text(monkeypatch):
    """Foto de producto → se reinyecta como '[el cliente envió una foto: ...]'
    y se delega al agente (ruta sigue en bot)."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("product_1"))

    assert len(loader.calls) == 1
    injected = loader.calls[0].message
    assert injected.startswith("[el cliente envió una foto:")
    assert "rosado" in injected.lower()
    # No se asignó a humano
    assert metadata.store["wa_5491111111111"].get("active_route") != "humano"


@pytest.mark.asyncio
async def test_payment_receipt_routes_to_human_and_acks(monkeypatch):
    """Comprobante de pago en turno de bot → se asigna al humano + ack al
    cliente. El agente NO resuelve el pago."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")

    sent: list[tuple] = []

    async def _fake_send(phone_number_id, to_number, text):  # noqa: ANN001
        sent.append((phone_number_id, to_number, text))

    monkeypatch.setattr(
        "src.platform.whatsapp.client.send_message", _fake_send, raising=False
    )

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore(seed={"active_route": "ventas"})
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    state = metadata.store["wa_5491111111111"]
    assert state["active_route"] == "humano"
    assert state["tag"] == "HUMANO"
    assert state["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"
    # Ack enviado al cliente
    assert len(sent) == 1
    assert "comprobante" in sent[0][2].lower()
    # La descripción del comprobante quedó en el historial para el humano
    assert any("comprobante de pago" in c.lower() for _, c in history.events)


@pytest.mark.asyncio
async def test_payment_receipt_already_human_does_not_reack(monkeypatch):
    """Si ya está en turno humano (caso común post register_order), NO
    re-asignamos ni mandamos ack: solo dejamos la descripción para el humano."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")

    sent: list[tuple] = []

    async def _fake_send(phone_number_id, to_number, text):  # noqa: ANN001
        sent.append((phone_number_id, to_number, text))

    monkeypatch.setattr(
        "src.platform.whatsapp.client.send_message", _fake_send, raising=False
    )

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore(seed={"active_route": "humano", "tag": "HUMANO"})
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    # No ack duplicado (el humano ya está atendiendo)
    assert sent == []
    # La descripción igual queda en el historial
    assert any("comprobante de pago" in c.lower() for _, c in history.events)


@pytest.mark.asyncio
async def test_vision_failure_reenters_placeholder(monkeypatch):
    """Visión deshabilitada / falla → placeholder para que el agente pida al
    cliente que escriba qué necesita (no se rompe el flujo)."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "off")
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("img_1"))

    assert len(loader.calls) == 1
    assert "no pude ver" in loader.calls[0].message.lower()
    assert "vision_failures" in metadata.store["wa_5491111111111"]


@pytest.mark.asyncio
async def test_caption_preserved_in_reinjection(monkeypatch):
    """Si la imagen viene con caption (texto del cliente), se preserva en la
    reinyección para que el agente lo tenga."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(
        _make_image("product_1", caption="¿tienen esta?")
    )

    assert "¿tienen esta?" in loader.calls[0].message


@pytest.mark.asyncio
async def test_inbound_image_persisted_and_url_in_history(monkeypatch, tmp_path):
    """Comportamiento clave del feature (no solo schema): la imagen del cliente
    se DESCARGA de Meta, se PERSISTE en el vault, y el evento del cliente en el
    historial lleva su `image_url` apuntando al endpoint del dashboard. Sin
    esto el operador humano solo tendría la descripción de texto de la visión,
    nunca la foto."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    # Vault temporal para el media store (no tocar el vault real del repo).
    monkeypatch.setattr(
        "src.platform.media.store.WORKSPACE_VAULT_DIR", tmp_path, raising=True
    )

    # Fake de la descarga de Meta — sin red.
    async def _fake_fetch(media_id):  # noqa: ANN001
        return (b"\xff\xd8\xff\xe0JFIF-fake-bytes", "image/jpeg")

    monkeypatch.setattr(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
        _fake_fetch,
        raising=True,
    )

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("product_1"))

    # 1. Los bytes quedaron en <vault>/<session>/media/.
    media_dir = tmp_path / "wa_5491111111111" / "media"
    files = list(media_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == "product_1.jpg"
    assert files[0].read_bytes() == b"\xff\xd8\xff\xe0JFIF-fake-bytes"

    # 2. El evento del cliente en el history lleva la URL del endpoint.
    assert (
        history.image_urls[-1]
        == "/api/dashboard/media/wa_5491111111111/product_1.jpg"
    )


@pytest.mark.asyncio
async def test_payment_receipt_image_persisted_for_human(monkeypatch, tmp_path):
    """El caso que motivó la HU: un comprobante de pago se persiste y su
    `image_url` llega al historial para que el humano verifique el pago
    MIRANDO la imagen, no solo leyendo la descripción."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    monkeypatch.setattr(
        "src.platform.media.store.WORKSPACE_VAULT_DIR", tmp_path, raising=True
    )

    async def _fake_fetch(media_id):  # noqa: ANN001
        return (b"receipt-bytes", "image/jpeg")

    monkeypatch.setattr(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
        _fake_fetch,
        raising=True,
    )

    async def _fake_send(phone_number_id, to_number, text):  # noqa: ANN001
        pass

    monkeypatch.setattr(
        "src.platform.whatsapp.client.send_message", _fake_send, raising=False
    )

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore(seed={"active_route": "ventas"})
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    # Imagen persistida — extensión del mime declarado por Meta (image/jpeg via
    # `_make_image`). El mime del fetch es solo fallback si el webhook no lo
    # trae; la cobertura de png/webp vive en test_media_store.py.
    media_dir = tmp_path / "wa_5491111111111" / "media"
    files = list(media_dir.iterdir())
    assert [f.name for f in files] == ["receipt_1.jpg"]
    # El evento del comprobante en el history lleva la URL para el humano.
    assert (
        history.image_urls[-1]
        == "/api/dashboard/media/wa_5491111111111/receipt_1.jpg"
    )
    # Y la conversación quedó asignada a humano (verificación de pago).
    assert metadata.store["wa_5491111111111"]["active_route"] == "humano"


@pytest.mark.asyncio
async def test_image_fetch_failure_does_not_break_reentry(monkeypatch, tmp_path):
    """Si la descarga de la imagen falla, el flujo NO se rompe: el agente igual
    recibe la descripción de texto, solo que sin `image_url` (degradado, no
    roto)."""
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    monkeypatch.setattr(
        "src.platform.media.store.WORKSPACE_VAULT_DIR", tmp_path, raising=True
    )

    async def _fake_fetch(media_id):  # noqa: ANN001
        return None  # Meta devolvió error / URL expirada

    monkeypatch.setattr(
        "src.platform.audio.meta_media_fetcher.fetch_media_bytes",
        _fake_fetch,
        raising=True,
    )

    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = _make_use_case(history, loader, metadata)

    await use_case._describe_image_and_reenter(_make_image("product_1"))

    # Reentry ocurrió (agente recibió la descripción), pero sin image_url.
    assert len(loader.calls) == 1
    assert history.image_urls[-1] is None
    assert not (tmp_path / "wa_5491111111111" / "media").exists()


def test_parse_kind_and_description_robust():
    """El parser TIPO/DESCRIPCION tolera formato imperfecto del modelo."""
    from src.platform.vision.litellm_adapter import _parse_kind_and_description
    from src.platform.vision.dtos import (
        VISION_KIND_OTHER,
        VISION_KIND_PAYMENT_RECEIPT,
    )

    kind, desc = _parse_kind_and_description(
        "TIPO: comprobante_pago\nDESCRIPCION: Transferencia Nequi por $34.000"
    )
    assert kind == VISION_KIND_PAYMENT_RECEIPT
    assert "34.000" in desc

    # Formato roto → cae a 'otro' con el texto crudo
    kind2, desc2 = _parse_kind_and_description("una vela rosada bonita")
    assert kind2 == VISION_KIND_OTHER
    assert desc2 == "una vela rosada bonita"
