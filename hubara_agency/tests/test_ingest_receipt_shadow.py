"""El ingest anota en sombra el veredicto del comprobante y nada cambia.

Tarjeta 33 del ciclo: el cliente recibe el mismo mensaje y la conversación pasa
al equipo con el mismo motivo, haya o no veredicto, y aunque anotarlo falle.
Datos inventados (la visión falsa lee «$34.000», referencia «1234»).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.plugins.chats.agent.sales.use_cases import ingest_inbound_message as ingest_module
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.shared.receipt_checks import read_checks
from tests import test_ingest_inbound_pdf_document as pdf
from tests.test_ingest_image_vision import (
    FakeHistoryStore,
    FakeLoadOrStart,
    FakeMetadataStore,
    _make_image,
    _reset_vision_cache,  # noqa: F401 — fixture autouse del arnés de visión
)

_SESSION = "wa_5491111111111"
_WITH_ORDER = {
    "active_route": "ventas",
    "episodes": [{"episode_id": "ep_001", "order_id": "ord_1", "closed_at_ms": 1}],
}


class _OnceEffects:
    """`claim` deja pasar cada efecto una sola vez por mensaje (como el store real)."""

    def __init__(self) -> None:
        self.claimed: set[tuple[str, str]] = set()

    def claim(self, wamid: str, effect: str) -> bool:
        if (wamid, effect) in self.claimed:
            return False
        self.claimed.add((wamid, effect))
        return True

    def done(self, wamid: str, effect: str) -> bool:
        return (wamid, effect) in self.claimed

    def mark(self, wamid: str, effect: str) -> None:
        self.claimed.add((wamid, effect))


async def _facts(order_id: str) -> Any:
    return SimpleNamespace(order_id=order_id, total_cop=34_000)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    texts: list[str] = []

    async def _fake_send(phone_number_id, to_number, text):  # noqa: ANN001
        texts.append(text)

    monkeypatch.setattr("src.platform.whatsapp.client.send_message", _fake_send, raising=False)
    return texts


def _use_case(seed: dict, *, effects: Any = None) -> tuple[IngestInboundMessage, FakeMetadataStore, FakeHistoryStore]:
    history, metadata = FakeHistoryStore(), FakeMetadataStore(seed=seed)
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
        order_facts=_facts,
        effects=effects,
    )
    return use_case, metadata, history


def _handed_to_the_team_as_always(metadata: FakeMetadataStore, sent: list[str], history: FakeHistoryStore) -> None:
    state = metadata.store[_SESSION]
    assert (state["active_route"], state["tag"]) == ("humano", "HUMANO")
    assert state["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"
    assert len(sent) == 1 and "recibimos tu comprobante" in sent[0].lower()
    assert any("comprobante de pago" in text.lower() for _, text in history.events)


async def test_a_receipt_is_noted_and_the_handoff_is_the_same_as_always(sent, _isolate_vault_dir: Path) -> None:
    use_case, metadata, history = _use_case(_WITH_ORDER)

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    _handed_to_the_team_as_always(metadata, sent, history)
    rows = read_checks(_isolate_vault_dir, _SESSION)
    assert [(r["verdict"], r["reason"], r["order_id"], r["source"]) for r in rows] == [
        ("coincide", None, "ord_1", "image")
    ]


async def test_if_noting_the_verdict_fails_the_handoff_still_goes_out(sent, monkeypatch, _isolate_vault_dir) -> None:
    async def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(ingest_module, "record_receipt_check", _boom)
    use_case, metadata, history = _use_case(_WITH_ORDER)

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    _handed_to_the_team_as_always(metadata, sent, history)
    assert read_checks(_isolate_vault_dir, _SESSION) == []


async def test_a_receipt_sent_while_the_team_has_the_chat_is_noted_too(sent, _isolate_vault_dir: Path) -> None:
    use_case, _, _ = _use_case({**_WITH_ORDER, "active_route": "humano", "tag": "HUMANO"})

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    assert sent == []
    assert [r["verdict"] for r in read_checks(_isolate_vault_dir, _SESSION)] == ["coincide"]


async def test_retrying_the_same_message_notes_the_receipt_once(sent, _isolate_vault_dir: Path) -> None:
    use_case, _, _ = _use_case(_WITH_ORDER, effects=_OnceEffects())

    await use_case._describe_image_and_reenter(_make_image("receipt_1"))
    await use_case._describe_image_and_reenter(_make_image("receipt_1"))

    assert len(read_checks(_isolate_vault_dir, _SESSION)) == 1


async def test_a_product_photo_is_not_a_receipt(sent, _isolate_vault_dir: Path) -> None:
    use_case, _, _ = _use_case(_WITH_ORDER)

    await use_case._describe_image_and_reenter(_make_image("product_1"))

    assert read_checks(_isolate_vault_dir, _SESSION) == []


async def test_a_pdf_is_noted_as_unreadable_and_goes_to_the_team_as_always(tmp_path, _isolate_vault_dir: Path) -> None:
    import src.platform.media.store as media_store

    store, send_mock = pdf.FakeMetadataStore(), AsyncMock()
    with (
        patch.object(media_store, "WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.platform.audio.meta_media_fetcher.fetch_media_bytes", new=pdf._fetch_ok()),
        patch("src.platform.whatsapp.client.send_message", new=send_mock),
    ):
        await pdf._use_case(pdf.FakeHistoryStore(), pdf.FakeLoadOrStart(), store).execute(pdf._make_document_message())

    assert store.store[_SESSION]["escalation_reason"] == "PAYMENT_VERIFICATION_PENDING"
    send_mock.assert_awaited_once()
    assert [(r["verdict"], r["source"]) for r in read_checks(_isolate_vault_dir, _SESSION)] == [("ilegible", "pdf")]
