"""Foto del pedido (panel derecho de Órdenes) + envío manual por el ETA.

El operador sube la foto del pedido listo. Se guarda en la conversación del
cliente (``<vault>/<session>/media/`` + ``metadata.order_photos[<order_id>]``)
porque es ahí donde la lee el ETA — que corre en otro contenedor con el mismo
vault — al pasar el pedido a "listo". El botón "Enviar ahora" emite
``OrderReadyPhotoRequestedEvent`` → transición del manifest → señal
``send_ready_photo`` en la sesión ETA del cliente.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orchestration import envelope_for
from src.platform.plugin_manifest import get_transitions
from src.plugins.orders.shared.contracts.events import OrderReadyPhotoRequestedEvent

SID = "wa_573001234567"
BACKEND_ID = "order_01PHOTO"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


# ---------- manifest ----------


def test_ready_photo_request_signals_the_customers_eta_session() -> None:
    env = envelope_for(
        OrderReadyPhotoRequestedEvent(session_id=SID, order_id=BACKEND_ID, requested_at_ms=1),
        source_plugin="orders",
        source_worker="reconcile",
    )
    [t] = [t for t in get_transitions("orders", "reconcile") if t.matches(env)]
    assert t.action.via == "signal_with_start"
    assert t.action.signal_name == "send_ready_photo"
    assert (t.action.target_plugin, t.action.target_worker) == ("eta", "eta")
    assert t.action.target_workflow == "HubaraEtaSessionWorkflow"
    assert t.action.workflow_id_template == "eta-{event.session_id}"
    assert t.action.input_mapping == {"session_id": "$.session_id", "order_id": "$.order_id"}


async def test_emitter_dispatches_the_request_event_for_the_session(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = []

    async def _dispatch(envelope, client):
        dispatched.append(envelope)

    async def _client():
        return object()

    monkeypatch.setattr("src.platform.orchestration.dispatch_envelope_with_client", _dispatch)
    monkeypatch.setattr("src.platform.temporal.client.get_temporal_client", _client)

    from temporalio.testing import ActivityEnvironment

    result = await ActivityEnvironment().run(
        emit_stage.emit_ready_photo_request_activity, BACKEND_ID, SID
    )

    assert result == "dispatched"
    [envelope] = dispatched
    assert envelope.event_type == "OrderReadyPhotoRequestedEvent"
    assert envelope.payload["session_id"] == SID
    assert envelope.payload["order_id"] == BACKEND_ID
    assert (envelope.source_plugin, envelope.source_worker) == ("orders", "reconcile")


# ---------- API ----------


class _QueryPort:
    def __init__(self, known: dict[str, str]) -> None:
        self.known = known  # id que llega (display o backend) → backend id

    async def get(self, order_id: str):
        backend = self.known.get(order_id)
        if backend is None:
            return None
        return SimpleNamespace(
            summary=SimpleNamespace(id=backend, phone="573001234567"),
            shipping_address=None,
        )


@pytest.fixture
def api(_isolate_vault_dir: Path, monkeypatch):
    vault = _isolate_vault_dir
    (vault / SID).mkdir(parents=True)
    (vault / SID / "metadata.json").write_text(json.dumps({"phone_number_id": "P"}), encoding="utf-8")
    requests: list[tuple] = []

    async def _resolve(*, backend_order_id, shipping_phone, vault_dir):
        return SID if backend_order_id == BACKEND_ID else None

    async def _start(order_id, session_id, request_id):
        requests.append((order_id, session_id, request_id))

    monkeypatch.setattr(
        "src.plugins.orders.api.get_order_query_port",
        lambda: _QueryPort({"#31": BACKEND_ID, BACKEND_ID: BACKEND_ID, "#40": "order_SIN_CHAT"}),
    )
    monkeypatch.setattr("src.plugins.orders.api._resolve_session_for_order", _resolve)
    monkeypatch.setattr("src.plugins.orders.api._start_ready_photo_request", _start)
    monkeypatch.setattr("src.plugins.orders.api._publish_orders_changed", lambda order_id=None: None)

    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), vault, requests


def _meta(vault: Path) -> dict:
    return json.loads((vault / SID / "metadata.json").read_text(encoding="utf-8"))


def _upload(client, order="%2331", data=JPEG, mime="image/jpeg"):
    return client.put(
        f"/api/orders/orders/{order}/photo",
        files={"file": ("pedido.jpg", data, mime)},
    )


def test_order_without_photo_reports_none(api):
    client, _, _ = api
    res = client.get("/api/orders/orders/%2331/photo")
    assert res.status_code == 200
    assert res.json() == {"order_id": BACKEND_ID, "photo": None, "has_conversation": True}


def test_upload_stores_the_photo_where_the_eta_reads_it(api):
    client, vault, _ = api
    res = _upload(client)

    assert res.status_code == 200, res.text
    photo = res.json()["photo"]
    entry = _meta(vault)["order_photos"][BACKEND_ID]
    assert (vault / SID / "media" / entry["filename"]).read_bytes() == JPEG
    assert entry["media_ref"] == f"/api/dashboard/media/{SID}/{entry['filename']}"
    assert entry["mime"] == "image/jpeg"
    assert entry["uploaded_at_ms"] == photo["uploaded_at_ms"] > 0
    assert photo["sent_at_ms"] is None
    # La vista la pinta desde su propio endpoint (no depende de chats ni
    # consulta Medusa por cada carga de la miniatura).
    assert photo["file_url"].startswith(f"/api/orders/order-photos/{SID}/{BACKEND_ID}")
    assert client.get(photo["file_url"]).content == JPEG


def test_photo_file_endpoint_rejects_traversal_and_unknown(api):
    client, _, _ = api
    _upload(client)
    assert client.get(f"/api/orders/order-photos/{SID}/order_OTRO").status_code == 404
    assert client.get(f"/api/orders/order-photos/..%2F{SID}/{BACKEND_ID}").status_code == 404


def test_eta_reads_exactly_what_orders_writes(api):
    """Contrato del vault entre plugins (orders escribe, eta lee): sin él, un
    cambio de forma de un lado deja el "listo" sin foto en silencio."""
    from src.plugins.eta.agent.eta.activities.tracking import order_photo_entry

    client, vault, _ = api
    _upload(client)

    entry = order_photo_entry(_meta(vault), BACKEND_ID)
    assert entry is not None
    assert (vault / SID / "media" / entry["filename"]).is_file()
    assert {"media_ref", "mime", "uploaded_at_ms"} <= entry.keys()


def test_replacing_the_photo_deletes_the_previous_file(api):
    client, vault, _ = api
    _upload(client)
    first = _meta(vault)["order_photos"][BACKEND_ID]["filename"]
    _upload(client, data=PNG, mime="image/png")
    second = _meta(vault)["order_photos"][BACKEND_ID]

    assert second["filename"] != first
    assert second["mime"] == "image/png"
    assert not (vault / SID / "media" / first).exists()


def test_delete_removes_photo_and_file(api):
    client, vault, _ = api
    _upload(client)
    filename = _meta(vault)["order_photos"][BACKEND_ID]["filename"]

    res = client.delete("/api/orders/orders/%2331/photo")

    assert res.status_code == 200
    assert res.json()["photo"] is None
    assert BACKEND_ID not in _meta(vault).get("order_photos", {})
    assert not (vault / SID / "media" / filename).exists()


@pytest.mark.parametrize(
    ("data", "mime", "status"),
    [
        (b"%PDF-1.4 no es foto", "application/pdf", 415),
        (b"no-es-jpeg", "image/jpeg", 415),  # el tipo miente: se miran los bytes
        (b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024), "image/jpeg", 413),  # > 5 MB de Meta
    ],
)
def test_rejects_what_whatsapp_would_not_accept_as_header(api, data, mime, status):
    client, vault, _ = api
    res = _upload(client, data=data, mime=mime)
    assert res.status_code == status
    assert "order_photos" not in _meta(vault)


def test_order_without_whatsapp_conversation_cannot_get_a_photo(api):
    client, _, _ = api
    assert client.get("/api/orders/orders/%2340/photo").json()["has_conversation"] is False
    res = _upload(client, order="%2340")
    assert res.status_code == 409
    assert "conversación" in res.json()["detail"]


def test_unknown_order_is_404(api):
    client, _, _ = api
    assert client.get("/api/orders/orders/%2399/photo").status_code == 404


def test_send_now_requests_the_eta_send(api):
    client, _, requests = api
    _upload(client)

    res = client.post("/api/orders/orders/%2331/photo/send", json={"request_id": "req-1"})

    assert res.status_code == 202, res.text
    assert requests == [(BACKEND_ID, SID, "req-1")]


def test_send_now_without_photo_is_rejected(api):
    client, _, requests = api
    res = client.post("/api/orders/orders/%2331/photo/send", json={})
    assert res.status_code == 409
    assert "foto" in res.json()["detail"]
    assert requests == []


def test_a_failed_eta_send_shows_on_the_panel(api):
    """El ETA deja el motivo en la foto (``last_send_error``); el panel lo lee."""
    client, vault, _ = api
    _upload(client)
    meta = _meta(vault)
    meta["order_photos"][BACKEND_ID]["last_send_error"] = "La plantilla aún no está aprobada en Meta."
    (vault / SID / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    photo = client.get("/api/orders/orders/%2331/photo").json()["photo"]

    assert photo["last_error"] == "La plantilla aún no está aprobada en Meta."
