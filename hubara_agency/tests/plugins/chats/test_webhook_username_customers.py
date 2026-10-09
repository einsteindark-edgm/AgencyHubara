"""Clientes con NOMBRE DE USUARIO de WhatsApp: Meta manda su mensaje SIN
teléfono (`from` / `contacts[].wa_id` omitidos) y solo con su id de Meta
(`from_user_id` / `contacts[].user_id`, el BSUID: `CO.9990000000000002`).

Bug (ledger, 2026-09-25 y otra vez 2026-10-09): el parser exigía `from`, el
webhook respondía 400 y el cliente nunca llegaba al bot. Halloween 07–08 oct:
Meta contó 8 conversaciones, entraron 5; las 3 restantes eran estos clientes.

Contrato:
* el mensaje entra; la conversación es `wa_<CC><id>` (el BSUID sin el punto:
  sigue siendo un nombre de directorio seguro del vault);
* si después el mismo cliente llega con teléfono, sigue en SU conversación;
* un cliente con teléfono que más adelante llega solo con el BSUID, también.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

_PHONE_ID = "100000000000001"
_BSUID = "CO.9990000000000002"
_ADDRESS = "CO9990000000000002"
_PHONE = "573001234567"


class _Ingest:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, parsed: Any, **kwargs: Any) -> None:
        self.calls.append(parsed)


def _msg(wamid: str, *, phone: str | None = None, user_id: str | None = None, **extra: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"id": wamid, "timestamp": "1789992000", "type": "text", "text": {"body": "hola"}}
    if phone is not None:
        msg["from"] = phone
    if user_id is not None:
        msg["from_user_id"] = user_id
    return msg | extra


def _body(msg: dict[str, Any], contacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"metadata": {"phone_number_id": _PHONE_ID}, "messages": [msg]}
    if contacts is not None:
        value["contacts"] = contacts
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from src.main import app
    from src.platform import config
    from src.plugins.chats.agent.sales.sender_identity_store import FilesystemSenderIdentity
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ingest = _Ingest()
    monkeypatch.setattr(api, "build_ingest_use_case", lambda: ingest)
    identity = FilesystemSenderIdentity(tmp_path / "_identity")
    monkeypatch.setattr(api, "build_sender_identity", lambda: identity)
    phones = FilesystemSenderIdentity(tmp_path / "_phones")
    monkeypatch.setattr(api, "build_phone_identity", lambda: phones)
    return TestClient(app), ingest


def test_a_customer_without_phone_reaches_the_bot_under_their_meta_id(harness) -> None:
    client, ingest = harness

    r = client.post("/api/webhook", json=_body(_msg("wamid.U1", user_id=_BSUID)))

    assert r.status_code == 200
    [parsed] = ingest.calls
    assert parsed.from_number == _ADDRESS  # → conversación wa_CO9990000000000002
    assert parsed.wa_user_id == _BSUID


def test_the_profile_name_of_a_customer_without_phone_comes_from_their_contact(harness) -> None:
    client, ingest = harness
    contacts = [{"profile": {"name": "  Valeria​  ", "username": "valeria.prueba"}, "user_id": _BSUID}]

    client.post("/api/webhook", json=_body(_msg("wamid.U1", user_id=_BSUID), contacts))

    assert ingest.calls[0].profile_name == "Valeria"


def test_the_profile_name_of_a_phone_customer_reaches_the_ingest(harness) -> None:
    """El parser del POST entero nunca leía `contacts[].profile.name` (solo lo
    hacía la versión de un mensaje, que el webhook ya no usa)."""
    client, ingest = harness
    contacts = [{"profile": {"name": "Ana"}, "wa_id": _PHONE}]

    client.post("/api/webhook", json=_body(_msg("wamid.P1", phone=_PHONE), contacts))

    assert ingest.calls[0].profile_name == "Ana"


def test_when_the_phone_shows_up_later_the_customer_stays_in_their_conversation(harness) -> None:
    """Meta incluye el teléfono cuando ya hablamos con el cliente: sin
    recordar el BSUID, su segundo mensaje abriría otra conversación (wa_57…)
    y el bot perdería el hilo."""
    client, ingest = harness

    client.post("/api/webhook", json=_body(_msg("wamid.U1", user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.U2", phone=_PHONE, user_id=_BSUID)))

    assert [m.from_number for m in ingest.calls] == [_ADDRESS, _ADDRESS]


def test_a_customer_who_started_without_phone_keeps_their_conversation_if_only_the_phone_comes(harness) -> None:
    """Premortem 2026-10-09: el teléfono que Meta empieza a mandar se
    descartaba sin recordarlo. Si después llega un mensaje solo con `from`
    (sin `from_user_id`), abría `wa_57…`: otra conversación, sin el borrador
    ni el formulario ni el pedido."""
    client, ingest = harness

    client.post("/api/webhook", json=_body(_msg("wamid.U1", user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.U2", phone=_PHONE, user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.U3", phone=_PHONE)))

    assert [m.from_number for m in ingest.calls] == [_ADDRESS, _ADDRESS, _ADDRESS]


def test_a_phone_that_already_has_its_conversation_keeps_it(harness, _isolate_vault_dir) -> None:
    """Revisión del premortem (2026-10-09): si el teléfono ya tiene su
    conversación (`wa_57…`, de antes del nombre de usuario), recordarlo como
    alias de la conversación sin teléfono se la quitaba: lo que llegara solo
    con `from` iba a parar a la otra, sin su historial ni sus pedidos."""
    client, ingest = harness
    (_isolate_vault_dir / f"wa_{_PHONE}").mkdir()

    client.post("/api/webhook", json=_body(_msg("wamid.U1", user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.U2", phone=_PHONE, user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.U3", phone=_PHONE)))

    assert [m.from_number for m in ingest.calls] == [_ADDRESS, _ADDRESS, _PHONE]


def test_a_phone_customer_who_later_arrives_only_with_their_meta_id_stays_in_their_conversation(harness) -> None:
    client, ingest = harness

    client.post("/api/webhook", json=_body(_msg("wamid.P1", phone=_PHONE, user_id=_BSUID)))
    client.post("/api/webhook", json=_body(_msg("wamid.P2", user_id=_BSUID)))

    assert [m.from_number for m in ingest.calls] == [_PHONE, _PHONE]


def test_a_phone_customer_without_meta_id_is_unchanged(harness) -> None:
    client, ingest = harness

    client.post("/api/webhook", json=_body(_msg("wamid.P1", phone=_PHONE)))

    assert ingest.calls[0].from_number == _PHONE
    assert ingest.calls[0].wa_user_id is None


@pytest.mark.parametrize(
    "user_id",
    [
        "../../etc",  # traversal
        "CO.15025/76",
        "CO.",  # sin cuerpo
        "9990000000000002",  # sin país
        "co.9990000000000002",  # el país va en mayúsculas (ISO)
        "CO.ENT.9990000000000002",  # un id "padre" no es quien escribe
        "CO." + "1" * 129,  # pasado el tope de Meta (128)
    ],
)
def test_a_message_with_neither_phone_nor_a_valid_meta_id_is_still_rejected(harness, user_id: str) -> None:
    client, ingest = harness

    r = client.post("/api/webhook", json=_body(_msg("wamid.X", user_id=user_id)))

    assert r.status_code == 400
    assert ingest.calls == []
