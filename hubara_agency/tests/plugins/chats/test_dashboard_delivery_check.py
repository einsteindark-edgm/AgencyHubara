"""El doble check de WhatsApp en el detalle de la conversación.

`/api/dashboard/sessions/{id}` lo leen el panel y la App Operador. Cada mensaje
SALIENTE (del bot o del operador) lleva `delivery`: lo que Meta informó por
webhook — enviado, entregado, leído o fallido con su código — o
`unconfirmed` («sin confirmación») si no llegó ningún estado. Nunca se inventa
un check. Los mensajes del cliente no lo llevan.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard
from src.plugins.chats.shared.delivery_status import DeliveryStatusLog

#: Sintético (forge: nunca un teléfono real en el repo).
_SID = "wa_573001234567"


@pytest.fixture
def client(tmp_path: Path):
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed_history(vault: Path, events: list[dict]) -> None:
    session = vault / _SID
    (session / "sessions").mkdir(parents=True, exist_ok=True)
    (session / "metadata.json").write_text(json.dumps({"tag": "INTERESADO"}), encoding="utf-8")
    (session / "sessions" / f"{_SID}.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )


def test_each_outbound_message_carries_what_whatsapp_reported(client) -> None:
    c, vault = client
    ts = "2026-10-15T02:40:00+00:00"
    _seed_history(
        vault,
        [
            {"role": "user", "content": "hola", "timestamp": ts, "wamid": "wamid.IN1"},
            # Un texto del bot sale como N burbujas, cada una con su wamid.
            {"role": "assistant", "content": "¡Hola!\n\n¿Qué buscas?", "timestamp": ts,
             "wamids": ["wamid.B1", "wamid.B2"]},
            {"role": "assistant", "sender": "human", "content": "Ya te ayudo", "timestamp": ts,
             "wamids": ["wamid.H1"]},
            {"role": "assistant", "kind": "ui_component", "component_kind": "products_list",
             "content": "El bot envió el catálogo", "timestamp": ts, "wamid": "wamid.C1"},
            # Texto de antes del cambio: sin wamid, sin forma de confirmarlo.
            {"role": "assistant", "content": "Mensaje viejo", "timestamp": ts},
        ],
    )
    log = DeliveryStatusLog(vault)
    log.append(_SID, wamid="wamid.B1", status="read", at_ms=1_760_496_300_000)
    log.append(_SID, wamid="wamid.B2", status="delivered", at_ms=1_760_496_240_000)
    log.append(_SID, wamid="wamid.H1", status="read", at_ms=1_760_496_360_000)
    log.append(_SID, wamid="wamid.C1", status="failed", at_ms=1_760_496_100_000,
               error_code=131047, error_title="Re-engagement message")

    messages = c.get(f"/api/dashboard/sessions/{_SID}").json()["messages"]

    customer, bot, human, card, legacy = messages
    assert "delivery" not in customer
    assert bot["delivery"] == {"status": "delivered", "at_ms": 1_760_496_240_000,
                               "error_code": None, "error_title": None}
    assert human["delivery"]["status"] == "read"
    assert card["delivery"] == {"status": "failed", "at_ms": 1_760_496_100_000,
                                "error_code": 131047, "error_title": "Re-engagement message"}
    assert legacy["delivery"]["status"] == "unconfirmed"


def test_a_meta_status_webhook_reaches_the_message_and_the_live_channel(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gotcha 1: no basta con que el schema lo permita — el backend lo EMITE.
    Webhook real de Meta (`statuses[]`) → el mensaje del bot muestra «leído» en
    el detalle que leen el panel y la app, y sale `chats.session_updated` por
    el SSE para que ambos lo pinten en vivo. La conversación tiene la forma del
    incidente 2026-10-15: episodio sin `outbound_messages`."""
    from src.main import app
    from src.platform import config
    from src.plugins.chats.agent.sales import composition
    from src.sdk.dashboardkit import get_dashboard_event_bus

    vault = _isolate_vault_dir
    _seed_history(
        vault,
        [{"role": "assistant", "content": "¡Hola!", "timestamp": "2026-10-15T02:40:00+00:00",
          "wamids": ["wamid.E1"]}],
    )
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    monkeypatch.setattr(composition, "_DELIVERY_STATUS_USE_CASE", None)
    # Sin outbound en el episodio, la búsqueda del costo reintenta ~3,5 s.
    monkeypatch.setattr(composition.build_ingest_delivery_status_use_case(), "_retry_delays", ())
    bus = get_dashboard_event_bus()
    queue = bus.subscribe()
    status = {"id": "wamid.E1", "status": "read", "timestamp": "1760496300",
              "recipient_id": _SID.removeprefix("wa_")}
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [
        {"field": "messages", "value": {"metadata": {"phone_number_id": "1"}, "statuses": [status]}}
    ]}]}

    try:
        assert TestClient(app).post("/api/webhook", json=body).status_code == 200
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        bus.unsubscribe(queue)

    detail_app = FastAPI()
    detail_app.include_router(dashboard.router, prefix="/api/dashboard")
    [message] = TestClient(detail_app).get(f"/api/dashboard/sessions/{_SID}").json()["messages"]
    assert message["delivery"] == {"status": "read", "at_ms": 1_760_496_300_000,
                                   "error_code": None, "error_title": None}
    assert any((e.domain, e.type, e.id) == ("chats", "session_updated", _SID) for e in events)
