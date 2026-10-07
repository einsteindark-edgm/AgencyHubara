"""Un error PASAJERO al leer `metadata.json` se reintenta en el ingest y en el
router (PR #393, séptima revisión).

El store no reintenta: hace un intento, sin esperas, y un error pasajero lanza.
El ingest sí tiene que reintentar. Corre en segundo plano DESPUÉS de responder
200 al webhook, así que Meta no lo reintenta, y un EMFILE de un milisegundo
dejaba al cliente sin respuesta.

La regla:
  * hasta 3 reintentos con espera ASÍNCRONA (50, 150 y 400 ms), solo ante
    EMFILE, ENFILE, ENOMEM, EAGAIN, ESTALE y EIO;
  * si sigue fallando, el mensaje queda en el historial, el ingest falla
    (`ingest_failed` en el ledger) y un ERROR dice que no se despachó;
  * el router no decide sobre una ruta que no pudo leer.
"""
from __future__ import annotations

import asyncio
import errno
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import LoadOrStartSalesSession

SID = "wa_573001234567"


class _BotWoken(Exception):
    """El router llegó a despertar al bot (pidió el cliente de Temporal)."""


class _FlakyReads:
    """El store de verdad, con sus primeras `failures` lecturas fallando con un
    error pasajero del sistema."""

    def __init__(self, store: FilesystemMetadataStore, failures: int, code: int = errno.EMFILE) -> None:
        self._store = store
        self.failures = failures
        self.code = code

    def read(self, session_id: str) -> dict[str, Any]:
        if self.failures:
            self.failures -= 1
            raise OSError(self.code, os.strerror(self.code))
        return self._store.read(session_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _History:
    def __init__(self) -> None:
        self.events: list[str] = []

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self.events.append(content)

    def read_events(self, session_id: str) -> list[dict]:
        return []


class _Readings:
    async def read(self, inbound: Inbound) -> Readings:
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False)


class _Router:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, **kw: Any) -> None:
        self.calls += 1


def _seeded(vault: Path) -> FilesystemMetadataStore:
    store = FilesystemMetadataStore(vault)
    store.write(SID, {"phone_number_id": "pnid-1", "active_route": "ventas", "tag": "INTERESADO"})
    return store


def _message() -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.x1",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text="hola, quiero una vela",
        media=None,
        timestamp=str(int(time.time())),
    )


async def _route(store: Any) -> list[bool]:
    """Corre el router de verdad; devuelve si despertó al bot."""
    woken: list[bool] = []

    async def wake_the_bot() -> Any:
        woken.append(True)
        raise _BotWoken

    with pytest.raises(_BotWoken):
        await LoadOrStartSalesSession(wake_the_bot, store).execute(SID, "hola", "pnid-1")
    return woken


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [errno.EMFILE, errno.EIO], ids=["EMFILE", "EIO"])
async def test_a_transient_error_reading_the_route_is_retried_and_the_message_is_dispatched(
    _isolate_vault_dir: Path, code: int
) -> None:
    flaky = _FlakyReads(_seeded(_isolate_vault_dir), failures=1, code=code)

    assert await _route(flaky) == [True], "un error pasajero de un instante dejó al cliente sin respuesta"


@pytest.mark.asyncio
async def test_the_retry_waits_without_blocking_the_loop(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = _FlakyReads(_seeded(_isolate_vault_dir), failures=2)
    blocking: list[float] = []
    monkeypatch.setattr(time, "sleep", blocking.append)
    ticks: list[float] = []

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.01)
            ticks.append(time.monotonic())

    tick_task = asyncio.create_task(ticker())
    started = time.monotonic()
    try:
        assert await _route(flaky) == [True]
    finally:
        tick_task.cancel()
    elapsed = time.monotonic() - started

    assert blocking == [], "el reintento esperó con time.sleep (frena el bucle)"
    assert elapsed >= 0.18, "no esperó entre intentos (50 + 150 ms)"
    assert len(ticks) >= 5, "el bucle quedó frenado mientras se esperaba"


@pytest.mark.asyncio
async def test_a_transient_error_in_the_ingests_first_read_is_retried(_isolate_vault_dir: Path) -> None:
    """Sin el reintento, el ingest seguía sin haber leído y no escribía lo
    suyo (ni la hora del último mensaje del cliente)."""
    store = _seeded(_isolate_vault_dir)
    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=_Router(),  # type: ignore[arg-type]
        metadata_store=_FlakyReads(store, failures=1),  # type: ignore[arg-type]
        readings=_Readings(),
    )

    await ingest.execute(_message())

    assert store.read(SID).get("last_inbound_message_id") == "wamid.x1", "un error de un instante dejó al ingest sin escribir"


@pytest.mark.asyncio
async def test_a_persistent_read_error_leaves_the_message_in_the_history_and_says_so(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seeded(_isolate_vault_dir)
    path = _isolate_vault_dir / SID / "metadata.json"
    before = path.read_bytes()
    real_read_text = Path.read_text

    def disk_errors(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name in ("metadata.json", "metadata.json.prev"):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", disk_errors)
    history = _History()
    woken: list[bool] = []

    async def wake_the_bot() -> Any:
        woken.append(True)
        raise _BotWoken

    ingest = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=LoadOrStartSalesSession(wake_the_bot, store),
        metadata_store=store,
        readings=_Readings(),
    )

    with structlog.testing.capture_logs() as logs, pytest.raises(OSError):
        await ingest.execute(_message())
    monkeypatch.setattr(Path, "read_text", real_read_text)

    assert len(history.events) == 1, "el mensaje del cliente no quedó en el historial"
    assert woken == [], "el router decidió sobre una ruta que no pudo leer"
    assert path.read_bytes() == before
    errors = [e for e in logs if e.get("log_level") == "error"]
    assert any(
        e["event"] == "no pude leer la ruta; el mensaje no se despachó" and e.get("session_id") == SID for e in errors
    ), errors


# --- la primera lectura del ingest falla del todo (octava revisión, M1) -------
# Tras sus reintentos el ingest no escribe lo que decidió sobre `{}`. Pero hay
# decisiones que NO dependen de esa lectura y tienen que quedar: la ruta humana
# por un comprobante PDF y la baja «NO MÁS». Se escriben con `update` sobre la
# lectura fresca; la cortesía del PDF sale solo si la ruta quedó escrita.


class _Temporal:
    """Temporal de mentira: anota si el router despachó el mensaje."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> Any:
        temporal = self

        class _Handle:
            async def terminate(self, reason: str | None = None) -> None:
                temporal.calls.append("terminate")

            async def describe(self) -> Any:
                raise RuntimeError("no existe")

            async def signal(self, *args: Any, **kwargs: Any) -> None:
                temporal.calls.append("signal")

        return _Handle()

    async def start_workflow(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append("start_workflow")


class _OptOutReadings:
    async def read(self, inbound: Inbound) -> Readings:
        return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=True)


def _pdf() -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.doc",
        from_number="573001234567",
        phone_number_id="pnid-1",
        text=None,
        media={"type": "document", "id": "doc-1", "mime_type": "application/pdf", "filename": "comprobante.pdf"},
        timestamp=str(int(time.time())),
    )


async def _ingest_with_first_read_failing(
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: WhatsAppMessage,
    *,
    readings: Any,
    failures: int = 4,
    disk_full: bool = False,
) -> tuple[FilesystemMetadataStore, _Temporal, AsyncMock]:
    import src.platform.whatsapp.client as wa_client

    async def pdf_bytes(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        return (b"%PDF-1.4 comprobante", "application/pdf")

    monkeypatch.setattr("src.platform.audio.meta_media_fetcher.fetch_media_bytes", pdf_bytes)
    told = AsyncMock(return_value=None)
    monkeypatch.setattr(wa_client, "send_message", told)
    monkeypatch.setattr("src.plugins.chats.agent.sales.metadata_reads.READ_RETRY_DELAYS_S", (0, 0, 0))
    store = _seeded(vault)
    if disk_full:
        import src.platform.state as state

        def no_space(*args: Any, **kwargs: Any) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(state, "atomic_write_json", no_space)
    temporal = _Temporal()

    async def client() -> Any:
        return temporal

    ingest = IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=LoadOrStartSalesSession(client, store),
        metadata_store=_FlakyReads(store, failures=failures),  # type: ignore[arg-type]
        readings=readings,
    )
    await ingest.execute(message)
    return store, temporal, told


@pytest.mark.asyncio
async def test_a_pdf_receipt_goes_to_a_human_even_if_the_first_read_never_worked(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, temporal, told = await _ingest_with_first_read_failing(
        _isolate_vault_dir, monkeypatch, _pdf(), readings=_Readings()
    )

    on_disk = store.read(SID)
    assert (on_disk["active_route"], on_disk.get("escalation_reason")) == ("humano", "PAYMENT_VERIFICATION_PENDING"), (
        "se le prometió un colega al cliente y nadie tiene el caso"
    )
    assert any("Recibí tu documento" in call.args[2] for call in told.await_args_list)
    assert temporal.calls == [], "el bot se despachó sobre una conversación que pasó a una persona"


@pytest.mark.asyncio
async def test_a_marketing_opt_out_stays_even_if_the_first_read_never_worked(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = WhatsAppMessage(
        message_id="wamid.x", from_number="573001234567", phone_number_id="pnid-1",
        text="NO MÁS", media=None, timestamp=str(int(time.time())),
    )

    store, _temporal, _told = await _ingest_with_first_read_failing(
        _isolate_vault_dir, monkeypatch, message, readings=_OptOutReadings()
    )

    assert store.read(SID).get("marketing_opt_out") is True, "se perdió la baja «NO MÁS»"


@pytest.mark.asyncio
async def test_the_pdf_courtesy_only_goes_out_if_the_human_route_was_written(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el disco no deja escribir la ruta humana, no se le dice al cliente
    «un colega lo revisa» (nadie tendría el caso)."""
    _store, _temporal, told = await _ingest_with_first_read_failing(
        _isolate_vault_dir, monkeypatch, _pdf(), readings=_Readings(), failures=0, disk_full=True
    )

    assert not any("Recibí tu documento" in call.args[2] for call in told.await_args_list), (
        "se prometió un colega sin que la ruta humana quedara escrita"
    )
