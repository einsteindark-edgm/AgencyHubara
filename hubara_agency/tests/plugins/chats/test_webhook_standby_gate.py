"""D1.4 — el webhook público rutea `standby` al oído pasivo y NUNCA al ingest
de Sales (que arranca el workflow); `messaging_handovers` se acepta (200) y se
ignora hasta D1.5. Cuerpos mezclados (`messages` + `standby`) van cada uno por
su puerta."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.plugins.chats import standby_payloads as P


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


@pytest.fixture
def harness(monkeypatch):
    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    sales_ingest, standby_ingest, delivery = _Recorder(), _Recorder(), _Recorder()
    monkeypatch.setattr(api, "build_ingest_use_case", lambda: sales_ingest)
    monkeypatch.setattr(api, "build_ingest_standby_use_case", lambda: standby_ingest)
    monkeypatch.setattr(api, "build_ingest_delivery_status_use_case", lambda: delivery)
    return TestClient(app), sales_ingest, standby_ingest, delivery


def test_standby_inbound_never_reaches_the_sales_ingest(harness) -> None:
    client, sales_ingest, standby_ingest, delivery = harness
    r = client.post("/api/webhook", json=P.inbound("hola"))
    assert r.status_code == 200
    assert sales_ingest.calls == []
    assert len(standby_ingest.calls) == 1
    event = standby_ingest.calls[0][0][0]
    assert event.messages[0].text == "hola"


def test_standby_echo_and_status_go_to_the_standby_ear_and_the_delivery_ingest(harness) -> None:
    client, sales_ingest, standby_ingest, delivery = harness
    assert client.post("/api/webhook", json=P.echo_text()).status_code == 200
    assert client.post("/api/webhook", json=P.status()).status_code == 200
    assert sales_ingest.calls == []
    assert len(standby_ingest.calls) == 1  # el eco; un body de solo statuses no toca el oído
    assert len(delivery.calls) == 1
    args = delivery.calls[0][0]
    assert args[0] == "wamid.STANDBY.ECHO.1" and args[1] == "delivered" and args[2]["category"] == "utility"


def test_messaging_handovers_is_accepted_and_ignored_until_d15(harness) -> None:
    client, sales_ingest, standby_ingest, delivery = harness
    assert client.post("/api/webhook", json=P.handover()).status_code == 200
    assert sales_ingest.calls == [] and standby_ingest.calls == [] and delivery.calls == []


def test_a_regular_messages_webhook_still_reaches_the_sales_ingest(harness) -> None:
    client, sales_ingest, standby_ingest, delivery = harness
    body = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "1", "phone_number_id": "PHONE_777"},
            "messages": [{"from": P.CUSTOMER, "id": "wamid.REG.1", "timestamp": "1757300000", "type": "text",
                          "text": {"body": "hola"}}],
        }}]}],
    }
    assert client.post("/api/webhook", json=body).status_code == 200
    assert len(sales_ingest.calls) == 1 and standby_ingest.calls == []
    # mezclado en un solo body: cada cambio por su puerta
    mixed = P.merged(body, P.inbound("desde standby"))
    assert client.post("/api/webhook", json=mixed).status_code == 200
    assert len(sales_ingest.calls) == 2 and len(standby_ingest.calls) == 1
