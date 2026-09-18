"""Meta puede agrupar VARIOS mensajes en un mismo POST al webhook (hasta 1000
updates, típico tras demoras/reintentos). Cada mensaje del cliente MUST llegar
al ingest — en orden.

Bug (auditoría 2026-09-18): el parser solo leía
`entry[0].changes[0].value.messages[0]`; el resto del batch se descartaba en
silencio — sin sesión en el vault, sin workflow, sin log. Candidato a explicar
conversaciones que Meta contó y nunca aparecieron en Hubara.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

_PHONE_ID = "100000000000001"
_ANA = "573001234567"
_BETO = "573000000001"


class _Ingest:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, parsed: Any, **kwargs: Any) -> None:
        self.calls.append(parsed)

    def received(self) -> list[tuple[str, str]]:
        return [(m.message_id, m.from_number) for m in self.calls]


def _msg(wamid: str, from_number: str, **overrides: Any) -> dict[str, Any]:
    base = {"id": wamid, "from": from_number, "timestamp": "1789992000", "type": "text", "text": {"body": f"hola {wamid}"}}
    return base | overrides


def _change(*messages: dict[str, Any]) -> dict[str, Any]:
    return {"field": "messages", "value": {"metadata": {"phone_number_id": _PHONE_ID}, "messages": list(messages)}}


def _entry(*changes: dict[str, Any], entry_id: str = "WABA") -> dict[str, Any]:
    return {"id": entry_id, "changes": list(changes)}


def _body(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": list(entries)}


_A, _B = _msg("wamid.A", _ANA), _msg("wamid.B", _BETO)

BATCH_SHAPES = {
    "varios_entry": _body(_entry(_change(_A), entry_id="WABA1"), _entry(_change(_B), entry_id="WABA2")),
    "varios_changes": _body(_entry(_change(_A), _change(_B))),
    "varios_messages_en_un_change": _body(_entry(_change(_A, _B))),
}


@pytest.fixture
def harness(monkeypatch):
    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ingest = _Ingest()
    monkeypatch.setattr(api, "build_ingest_use_case", lambda: ingest)
    return TestClient(app), ingest


@pytest.mark.parametrize("shape", list(BATCH_SHAPES))
def test_every_message_of_a_batched_post_reaches_the_ingest_in_order(harness, shape: str) -> None:
    client, ingest = harness

    r = client.post("/api/webhook", json=BATCH_SHAPES[shape])

    assert r.status_code == 200
    assert ingest.received() == [("wamid.A", _ANA), ("wamid.B", _BETO)]


def test_two_messages_of_the_same_customer_are_ingested_in_the_order_meta_sent_them(harness) -> None:
    client, ingest = harness

    client.post("/api/webhook", json=_body(_entry(_change(_msg("wamid.1", _ANA), _msg("wamid.2", _ANA)), _change(_msg("wamid.3", _ANA)))))

    assert [m.message_id for m in ingest.calls] == ["wamid.1", "wamid.2", "wamid.3"]
    assert [m.text for m in ingest.calls] == ["hola wamid.1", "hola wamid.2", "hola wamid.3"]


def test_an_invalid_message_does_not_take_down_the_rest_of_the_post(harness) -> None:
    client, ingest = harness
    broken = _msg("wamid.ROTO", _ANA, text={})  # text sin body

    r = client.post("/api/webhook", json=_body(_entry(_change(broken, _B))))

    assert r.status_code == 200  # un 400 haría que Meta reintente TODO el POST (y re-entregue wamid.B)
    assert ingest.received() == [("wamid.B", _BETO)]


def test_a_change_with_a_broken_envelope_does_not_take_down_its_siblings(harness) -> None:
    client, ingest = harness
    no_metadata = {"field": "messages", "value": {"messages": [_A]}}

    r = client.post("/api/webhook", json=_body(_entry(no_metadata, _change(_B))))

    assert r.status_code == 200
    assert ingest.received() == [("wamid.B", _BETO)]


def test_an_ingest_that_blows_up_does_not_starve_the_next_message(monkeypatch) -> None:
    """Starlette corre los background tasks en serie y CORTA en la primera
    excepción: sin aislar cada ingest, un fallo en A dejaría a B sin ingerir."""
    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    class _FailsOnAna(_Ingest):
        async def execute(self, parsed: Any, **kwargs: Any) -> None:
            await super().execute(parsed)
            if parsed.from_number == _ANA:
                raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ingest = _FailsOnAna()
    monkeypatch.setattr(api, "build_ingest_use_case", lambda: ingest)

    r = TestClient(app, raise_server_exceptions=False).post("/api/webhook", json=BATCH_SHAPES["varios_changes"])

    assert r.status_code == 200
    assert ingest.received() == [("wamid.A", _ANA), ("wamid.B", _BETO)]


def test_a_delivery_status_that_blows_up_does_not_starve_the_inbound_messages(harness, monkeypatch) -> None:
    """Los statuses se encolan ANTES que los mensajes: un status roto no puede
    dejar sin ingerir al cliente que escribió en el mismo POST."""
    from src.plugins.chats.api import sales as api

    class _BrokenDelivery:
        async def execute(self, *args: Any, **kwargs: Any) -> None:
            raise OSError("vault read-only")

    client, ingest = harness
    monkeypatch.setattr(api, "build_ingest_delivery_status_use_case", lambda: _BrokenDelivery())
    statuses = {"field": "messages", "value": {"metadata": {"phone_number_id": _PHONE_ID}, "statuses": [{"id": "wamid.OUT.1", "status": "delivered", "timestamp": "1789992000", "recipient_id": _ANA}]}}

    r = TestClient(client.app, raise_server_exceptions=False).post("/api/webhook", json=_body(_entry(statuses, _change(_B))))

    assert r.status_code == 200
    assert ingest.received() == [("wamid.B", _BETO)]


def test_a_post_with_nothing_usable_is_still_a_400(harness) -> None:
    """Contrato previo intacto: si NINGÚN mensaje del POST se pudo parsear, 400
    (`malformed payload`) — igual que cuando el único mensaje venía roto."""
    client, ingest = harness
    broken = _msg("wamid.ROTO", _ANA, text={})

    r = client.post("/api/webhook", json=_body(_entry(_change(broken))))

    assert r.status_code == 400
    assert "malformed payload" in r.json()["detail"]
    assert ingest.calls == []
