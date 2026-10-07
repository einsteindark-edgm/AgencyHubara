"""El ingest frente a un `metadata.json` dañado (decisión del operador,
2026-10-06, PR #393).

Un archivo dañado es un problema técnico: no pasa nada al equipo humano ni se
traban las escrituras. El store se recupera solo con la última copia buena
(`metadata.json.prev`) y deja una alerta: la conversación sigue como estaba en
ESA copia, que va una escritura atrás (N-1). Si lo último que se escribió fue
la toma del operador, un daño justo después la PIERDE (el bot puede contestar
hasta que el operador la tome otra vez); acá la copia buena ya tiene la ruta
humana.

Un error de lectura PASAJERO (EMFILE, EIO…) no es daño: la escritura del
ingest falla, no toca nada y lo suyo queda pendiente para la siguiente.
"""
from __future__ import annotations

import errno
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
    async def read(self, inbound: Inbound) -> Readings:
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)


def _ingest(store: FilesystemMetadataStore, router: Any) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=router,  # type: ignore[arg-type]
        metadata_store=store,
        readings=_Readings(),
    )


def _truncate(path: Path) -> None:
    path.write_text(json.dumps(_HUMAN_SESSION)[:-3], encoding="utf-8")


def _deny_reading(path: Path) -> None:
    path.write_text(json.dumps(_HUMAN_SESSION), encoding="utf-8")
    os.chmod(path, 0)


_DAMAGE = [
    pytest.param(_truncate, id="truncado"),
    pytest.param(
        _deny_reading,
        id="permisos-000",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", _DAMAGE)
async def test_a_text_over_a_damaged_metadata_stays_with_the_human_and_wakes_no_bot(
    _isolate_vault_dir: Path, damage: Any
) -> None:
    path = _isolate_vault_dir / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.with_name("metadata.json.prev").write_text(json.dumps(_HUMAN_SESSION), encoding="utf-8")
    damage(path)
    store = FilesystemMetadataStore(_isolate_vault_dir)
    router = _RouteAtDispatch(store)

    await _ingest(store, router).execute(
        WhatsAppMessage(
            message_id="wamid.in",
            from_number="573001234567",
            phone_number_id="pnid-1",
            text="hola?",
            media=None,
            timestamp=str(int(time.time())),
        )
    )

    assert router.routes == ["humano"], "el bot contestaría una conversación del humano"
    metadata = store.read(SID)
    assert (metadata["active_route"], metadata["tag"]) == ("humano", "HUMANO")
    assert metadata["motivo"] == _HUMAN_SESSION["motivo"], "se le pasó al equipo por un archivo dañado"
    assert "escalation_reason" not in metadata
    assert not [e for e in metadata.get("status_history", []) if e.get("tag") == "HUMANO"]
    assert metadata["registered_order"] == {"order_id": "order_1"}
    assert metadata["last_inbound_message_id"] == "wamid.in", "la conversación siguió escribiendo"


@pytest.mark.asyncio
async def test_a_pdf_receipt_goes_to_a_human_even_if_the_metadata_gets_damaged(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El PDF (comprobante probable) pasa la conversación a un humano y al
    cliente se le dice «un colega lo revisa». Si el documento se daña
    mientras se baja el PDF, el paso a humano igual queda escrito, sobre la
    última copia buena."""
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
    message = WhatsAppMessage(
        message_id="wamid.doc",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text=None,
        media={"type": "document", "id": "doc-1", "mime_type": "application/pdf", "filename": "comprobante.pdf"},
        timestamp=str(int(time.time())),
    )

    await _ingest(store, router).execute(message)

    told = [call.args[2] for call in sent.await_args_list]
    assert any("colega" in text for text in told), told
    assert router.routes == ["humano"], "al cliente se le dijo «un colega lo revisa» y nadie tiene el caso"
    metadata = store.read(SID)
    assert metadata["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"
    assert metadata["registered_order"] == {"order_id": "order_1"}


def _failing_reads(monkeypatch: pytest.MonkeyPatch, name: str, times: int) -> None:
    real_read_text = Path.read_text
    left = {"n": times}

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == name and left["n"] > 0:
            left["n"] -= 1
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_a_write_that_failed_is_still_pending_and_the_session_untouched(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión de la recuperación (1b6f9b83): tres EMFILE seguidos sobre un
    documento SANO lo daban por dañado, lo apartaban y escribían la copia
    vieja: la toma del humano (la última escritura) quedaba en el apartado y
    en disco `ventas`. Ahora la escritura falla, la sesión no se toca y lo del
    ingest sigue pendiente para la escritura siguiente (no se da por escrito)."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, {"active_route": "ventas", "tag": "NO_ETIQUETADO"})
    store.update(SID, lambda d: {**d, "active_route": "humano", "tag": "HUMANO"})  # la toma: última escritura
    path = _isolate_vault_dir / SID / "metadata.json"
    before = path.read_bytes()
    ingest = IngestInboundMessage(
        history_store=None,  # type: ignore[arg-type]
        load_session=None,  # type: ignore[arg-type]
        metadata_store=store,
    )
    base: dict[str, Any] = {"active_route": "humano", "tag": "HUMANO"}
    metadata: dict[str, Any] = {**base, "first_touch_origin": "direct"}

    _failing_reads(monkeypatch, "metadata.json", 1)  # sin reintentos: uno basta
    ingest._safe_write_metadata(SID, metadata, base)  # falla (error pasajero)

    assert path.read_bytes() == before, "un error pasajero pisó la toma del humano"
    assert not list(path.parent.glob("metadata.json.damaged-*"))
    metadata["last_inbound_message_id"] = "wamid.2"
    ingest._safe_write_metadata(SID, metadata, base)
    on_disk = store.read(SID)
    assert (on_disk["active_route"], on_disk.get("first_touch_origin"), on_disk["last_inbound_message_id"]) == (
        "humano",
        "direct",
        "wamid.2",
    ), "se perdió lo de la escritura que falló"



# --- una escritura auxiliar que falla no pierde el mensaje (sexta revisión) -----


class _RecordingHistory(_History):
    def __init__(self) -> None:
        self.events: list[str] = []

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self.events.append(content)


class _BotWoken(Exception):
    """El router llegó a despertar al bot (pidió el cliente de Temporal)."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["hola, quiero una vela", "hola ref:cart_01ABCDEFGHIJKLMNOPQRSTUV"],
    ids=["texto", "carrito-web"],
)
async def test_an_auxiliary_write_that_fails_does_not_lose_the_message(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """Las escrituras auxiliares del ingest y del router (captura del carrito,
    del ref, de la tarjeta y del cupón; el `phone_number_id`) son de mejor
    esfuerzo: si el disco no deja escribir, se registra y se sigue. El mensaje
    queda en el historial y el bot se despierta igual. Antes, la excepción
    cortaba el ingest, que corre después de responder 200 al webhook: Meta no
    lo reintenta."""
    import src.platform.state as state
    from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import LoadOrStartSalesSession

    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, {"phone_number_id": "pnid-1", "active_route": "ventas", "tag": "INTERESADO"})
    path = _isolate_vault_dir / SID / "metadata.json"
    before = path.read_bytes()

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(state, "atomic_write_json", disk_full)
    woken: list[bool] = []

    async def wake_the_bot() -> Any:
        woken.append(True)
        raise _BotWoken

    history = _RecordingHistory()
    ingest = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=LoadOrStartSalesSession(wake_the_bot, store),
        metadata_store=store,
        readings=_Readings(),
    )

    with pytest.raises(_BotWoken):
        await ingest.execute(
            WhatsAppMessage(
                message_id="wamid.x1",
                from_number="573001234567",
                phone_number_id="pnid-1",
                text=text,
                media=None,
                timestamp=str(int(time.time())),
            )
        )

    assert history.events, "el mensaje del cliente no quedó en el historial"
    assert woken, "el bot no se despertó"
    assert path.read_bytes() == before



@pytest.mark.asyncio
async def test_an_ingest_that_could_not_read_the_metadata_does_not_write_what_it_decided_on_nothing(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La lectura inicial del ingest es de mejor esfuerzo (el mensaje no se
    pierde). Con un error pasajero ahí, el ingest decidía sobre `{}` y lo
    escribía: pisaba el origen de la conversación (el anuncio que la trajo).
    Ahora no escribe nada de lo suyo; el mensaje va al historial y al router,
    que relee el documento y decide con lo que hay de verdad."""
    store = FilesystemMetadataStore(_isolate_vault_dir)
    store.write(SID, {**_HUMAN_SESSION, "active_route": "ventas", "tag": "INTERESADO"})
    store.write(SID, {**_HUMAN_SESSION, "origin": {"source_type": "ad", "source_id": "AD_1"}})
    path = _isolate_vault_dir / SID / "metadata.json"
    before = path.read_bytes()
    _failing_reads(monkeypatch, "metadata.json", 1)
    router = _RouteAtDispatch(store)
    history = _RecordingHistory()
    ingest = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=router,  # type: ignore[arg-type]
        metadata_store=store,
        readings=_Readings(),
    )

    await ingest.execute(
        WhatsAppMessage(
            message_id="wamid.in",
            from_number="573001234567",
            phone_number_id="pnid-1",
            text="hola?",
            media=None,
            timestamp=str(int(time.time())),
        )
    )

    assert path.read_bytes() == before, "el ingest escribió lo que decidió sobre un documento que no pudo leer"
    assert history.events, "el mensaje del cliente no quedó en el historial"
    assert router.routes == ["humano"]
