"""El ingest frente a un `metadata.json` dañado (decisión del operador,
2026-10-06, PR #393).

Un archivo dañado es un problema técnico: no pasa nada al equipo humano ni se
traban las escrituras. El store se recupera solo con la última copia buena
(`metadata.json.prev`) y deja una alerta; la conversación sigue como estaba
(en humano, si estaba en humano; el bot no se despierta).
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


class _SkipsTheFirstWrite(FilesystemMetadataStore):
    """Un store que no escribió la primera vez (devolvió None)."""

    def __init__(self, vault: Path) -> None:
        super().__init__(vault)
        self.calls = 0

    def write_merged(self, session_id: str, **kw: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return None
        return super().write_merged(session_id, **kw)


def test_a_write_that_did_not_happen_is_still_pending(_isolate_vault_dir: Path) -> None:
    """Segunda revisión del PR #393: tras una escritura que no se hizo, el
    ingest daba lo suyo por escrito (`base` = sus cambios) y la escritura
    siguiente ya no los llevaba. Solo cuenta como escrito lo que se escribió."""
    store = _SkipsTheFirstWrite(_isolate_vault_dir)
    store.write(SID, {"active_route": "humano", "tag": "HUMANO"})
    ingest = IngestInboundMessage(
        history_store=None,  # type: ignore[arg-type]
        load_session=None,  # type: ignore[arg-type]
        metadata_store=store,
    )
    base: dict[str, Any] = {"active_route": "humano", "tag": "HUMANO"}
    metadata: dict[str, Any] = {**base, "first_touch_origin": "direct"}

    ingest._safe_write_metadata(SID, metadata, base)  # no se escribió
    metadata["last_inbound_message_id"] = "wamid.2"
    ingest._safe_write_metadata(SID, metadata, base)

    on_disk = store.read(SID)
    assert on_disk.get("first_touch_origin") == "direct", "se perdió lo de la escritura que no se hizo"
    assert on_disk["last_inbound_message_id"] == "wamid.2"
