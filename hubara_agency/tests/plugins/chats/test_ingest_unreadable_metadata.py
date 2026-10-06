"""El ingest frente a un `metadata.json` ilegible (segunda revisión del PR #393).

Una lectura fallida no es una sesión vacía. El ingest no puede escribir encima
su vista mínima (`{last_inbound_message_id, origen…}`): se perdían
`active_route=humano`, la etiqueta, el pedido registrado y el teléfono, y el
bot le contestaba a una conversación del humano.

  * Ilegible desde el principio: el original queda al lado, tal cual
    (`metadata.json.unreadable-<ms>`), y la conversación pasa a un humano
    con motivo «metadata ilegible»: aparece en la bandeja y el bot no contesta
    sin memoria.
  * Se vuelve ilegible mientras el ingest espera (Jev): no se toca.
  * Una escritura que no se hizo no cuenta como hecha: lo de esa escritura
    sigue pendiente para la siguiente.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

SID = "wa_573001234567"

_HUMAN_SESSION = {
    "phone_number_id": "pnid-1",
    "active_route": "humano",
    "tag": "HUMANO",
    "motivo": "Comprobante de pago: verificar",
    "registered_order": {"order_id": "order_1"},
    "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None, "order_id": "order_1"}],
}


class _History:
    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        pass

    def read_events(self, session_id: str) -> list[dict]:
        return []


class _RouteAtDispatch:
    """El router de verdad decide por `active_route`: este anota cuál vería."""

    def __init__(self, store: FilesystemMetadataStore) -> None:
        self._store = store
        self.routes: list[str] = []

    async def execute(self, *, session_id: str, **kw: Any) -> None:
        self.routes.append(self._store.read(session_id).get("active_route", "ventas"))


class _Readings:
    """Las lecturas de Jev; `during` corre mientras «se espera»."""

    def __init__(self, during: Any = None) -> None:
        self._during = during

    async def read(self, inbound: Inbound) -> Readings:
        if self._during is not None:
            self._during()
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)


def _text(text: str = "hola?") -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.in",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text=text,
        media=None,
        timestamp=str(int(time.time())),
    )


def _truncate(path: Path) -> None:
    path.write_text(json.dumps(_HUMAN_SESSION)[:-3], encoding="utf-8")


def _deny_reading(path: Path) -> None:
    os.chmod(path, 0)


def _bytes(path: Path) -> bytes:
    os.chmod(path, 0o644)
    return path.read_bytes()


_UNREADABLE = [
    pytest.param(_truncate, id="truncado"),
    pytest.param(
        _deny_reading,
        id="sin-permiso",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("break_it", _UNREADABLE)
async def test_a_text_over_an_unreadable_metadata_keeps_it_and_hands_the_chat_to_a_human(
    _isolate_vault_dir: Path, break_it: Any
) -> None:
    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, _HUMAN_SESSION)
    path = _isolate_vault_dir / SID / "metadata.json"
    break_it(path)
    original = _bytes(path)
    break_it(path)
    router = _RouteAtDispatch(store)
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=router,  # type: ignore[arg-type]
        metadata_store=store,
        readings=_Readings(),
    )

    await ingest.execute(_text())

    backups = sorted(path.parent.glob("metadata.json.unreadable-*"))
    assert len(backups) == 1, "el ingest escribió encima del original sin guardarlo"
    assert _bytes(backups[0]) == original
    metadata = store.read(SID)
    assert (metadata.get("active_route"), metadata.get("tag")) == ("humano", "HUMANO"), metadata
    assert "ilegible" in metadata["motivo"]
    assert router.routes == ["humano"], "el bot contestaría sin memoria"


@pytest.mark.asyncio
async def test_a_metadata_that_turns_unreadable_during_the_wait_is_not_rewritten(
    _isolate_vault_dir: Path,
) -> None:
    """Se leyó bien y se volvió ilegible mientras se esperaba a Jev (otro
    escritor a medias, un disco que falla): el ingest no escribe su vista
    vieja encima. Los bytes quedan iguales."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, {**_HUMAN_SESSION, "active_route": "ventas", "tag": "NO_ETIQUETADO"})
    path = _isolate_vault_dir / SID / "metadata.json"
    broken = b'{"active_route": "humano", "tag": "HUMANO", "motivo": "Lo atiendo'
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=_RouteAtDispatch(store),  # type: ignore[arg-type]
        metadata_store=store,
        readings=_Readings(during=lambda: path.write_bytes(broken)),
    )

    await ingest.execute(_text())

    assert path.read_bytes() == broken, "el ingest reescribió un metadata.json que no pudo leer"


@pytest.mark.asyncio
async def test_a_pdf_receipt_goes_to_a_human_even_if_the_metadata_turns_unreadable(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El PDF (comprobante probable) pasa la conversación a un humano y al
    cliente se le dice «un colega lo revisa». Si el documento se vuelve
    ilegible mientras se baja el PDF, el paso a humano igual queda escrito
    (como la foto-comprobante): si no, nadie tendría el caso."""
    from unittest.mock import AsyncMock

    import src.platform.whatsapp.client as wa_client

    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, {**_HUMAN_SESSION, "active_route": "ventas", "tag": "NO_ETIQUETADO"})
    path = _isolate_vault_dir / SID / "metadata.json"

    async def download_while_it_breaks(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        path.write_text('{"active_route": "ventas", "episodes": [', encoding="utf-8")
        return (b"%PDF-1.4 comprobante", "application/pdf")

    monkeypatch.setattr("src.platform.audio.meta_media_fetcher.fetch_media_bytes", download_while_it_breaks)
    sent = AsyncMock(return_value=None)
    monkeypatch.setattr(wa_client, "send_message", sent)
    router = _RouteAtDispatch(store)
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=router,  # type: ignore[arg-type]
        metadata_store=store,
        readings=_Readings(),
    )
    message = WhatsAppMessage(
        message_id="wamid.doc",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text=None,
        media={"type": "document", "id": "doc-1", "mime_type": "application/pdf", "filename": "comprobante.pdf"},
        timestamp=str(int(time.time())),
    )

    await ingest.execute(message)

    told = [call.args[2] for call in sent.await_args_list]
    assert any("colega" in text for text in told), told
    assert router.routes == ["humano"], "al cliente se le dijo «un colega lo revisa» y nadie tiene el caso"
    assert store.read(SID)["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"


def test_a_write_that_did_not_happen_is_still_pending(_isolate_vault_dir: Path) -> None:
    """Bloqueante de la segunda revisión: tras una escritura saltada (documento
    ilegible), el ingest daba lo suyo por escrito (`base` = sus cambios). Si el
    documento vuelve a leerse en el mismo `execute`, esos cambios ya no
    contaban como cambios y se perdían."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    path = _isolate_vault_dir / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"active_route": "humano", "tag": "HU', encoding="utf-8")
    ingest = IngestInboundMessage(
        history_store=None,  # type: ignore[arg-type]
        load_session=None,  # type: ignore[arg-type]
        metadata_store=store,
    )
    base: dict[str, Any] = {}  # lo que leyó: nada (ilegible)
    metadata: dict[str, Any] = {"first_touch_origin": "direct"}

    ingest._safe_write_metadata(SID, metadata, base)  # no se escribe: ilegible
    path.write_text(json.dumps({"active_route": "humano", "tag": "HUMANO"}), encoding="utf-8")  # lo reparan
    metadata["last_inbound_message_id"] = "wamid.2"
    ingest._safe_write_metadata(SID, metadata, base)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk.get("first_touch_origin") == "direct", "se perdió lo de la escritura que no se hizo"
    assert (on_disk["active_route"], on_disk["tag"], on_disk["last_inbound_message_id"]) == (
        "humano",
        "HUMANO",
        "wamid.2",
    )
