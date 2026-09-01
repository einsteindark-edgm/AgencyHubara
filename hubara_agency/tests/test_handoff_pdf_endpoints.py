"""Envío de PDFs (comprobantes de pago) del operador — dashboard handoff.

Extiende el flujo de fotos en dos fases (ver test_handoff_media_endpoints.py)
para `application/pdf`:

  * `POST /media` acepta PDFs (magic `%PDF-`, cap propio de tamaño) y registra
    el attachment con `kind="document"` + el `filename` original — el nombre
    es lo que el cliente ve en la burbuja de WhatsApp.
  * `POST /messages` con un attachment `kind="document"` envía vía
    `send_document_to_session` (type=document de WhatsApp, NO type=image) y
    persiste el evento humano con `document_url` + `document_filename`.

Los attachments legacy (registrados sin `kind`) siguen yendo por el path de
imagen — cubierto por los tests existentes de fotos.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.platform.whatsapp.dtos import OutboundResult


@pytest.fixture
def client_and_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    with (
        patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.plugins.chats.api.dashboard_composition.WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.platform.media.store.WORKSPACE_VAULT_DIR", tmp_path),
        patch(
            "src.platform.session_history.store.FilesystemMessageHistoryStore.__init__",
            lambda self, vault_dir: setattr(self, "_vault_dir", tmp_path),
        ),
    ):
        import src.plugins.chats.api.dashboard_composition as comp

        comp._METADATA_STORE = None
        comp._HISTORY_STORE = None
        from src.main import app

        yield TestClient(app), tmp_path


def _write_metadata(vault, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read_metadata(vault, session_id: str) -> dict:
    p = vault / session_id / "metadata.json"
    return json.loads(p.read_text(encoding="utf-8"))


_FUTURE_MS = 9_999_999_999_000
_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


# ---------- POST /media con PDF (fase A: upload) ----------


def test_upload_pdf_persists_and_registers_document_kind(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p1", {"active_route": "humano"})

    upload_mock = AsyncMock(return_value="meta-media-pdf-1")
    with patch("src.plugins.chats.api.handoff.upload_media", new=upload_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_p1/media",
            files={"file": ("comprobante.pdf", _PDF_BYTES, "application/pdf")},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attachment_id"] == "meta-media-pdf-1"
    assert body["media_ref"].endswith(".pdf")
    # Subido a Meta con el mime correcto (Meta valida type=mime real).
    args = upload_mock.await_args.args
    assert args[1] == _PDF_BYTES
    assert args[2] == "application/pdf"
    # Persistido en disco como .pdf.
    filename = body["media_ref"].split("/")[-1]
    stored = vault / "wa_p1" / "media" / filename
    assert stored.exists()
    assert stored.read_bytes() == _PDF_BYTES
    # Registrado como documento con su nombre visible.
    entry = _read_metadata(vault, "wa_p1")["outbound_media"]["meta-media-pdf-1"]
    assert entry["kind"] == "document"
    assert entry["filename_display"] == "comprobante.pdf"


def test_upload_pdf_display_name_is_sanitized(client_and_vault):
    """El filename del multipart lo controla el cliente HTTP: componentes de
    path se descartan y un nombre vacío cae a un default servible."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p2", {"active_route": "humano"})

    with patch(
        "src.plugins.chats.api.handoff.upload_media",
        new=AsyncMock(return_value="m-1"),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p2/media",
            files={"file": ("../../etc/passwd.pdf", _PDF_BYTES, "application/pdf")},
        )
    assert res.status_code == 200
    entry = _read_metadata(vault, "wa_p2")["outbound_media"]["m-1"]
    assert "/" not in entry["filename_display"]
    assert ".." not in entry["filename_display"]
    assert entry["filename_display"].endswith(".pdf")


def test_upload_pdf_rejects_spoofed_bytes(client_and_vault):
    """content-type application/pdf con bytes que NO empiezan con %PDF- → 415
    (simetría con el sniff de JPEG/PNG, PM-B7)."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p3", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_p3/media",
            files={"file": ("evil.pdf", b"#!/bin/sh\necho pwned", "application/pdf")},
        )
    assert res.status_code == 415


def test_upload_pdf_rejects_oversize(client_and_vault):
    """Los PDFs tienen su propio cap (10 MB) — más alto que el de imagen
    (5 MB post-compresión) pero acotado: un comprobante bancario pesa KBs."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p4", {"active_route": "humano"})
    big = b"%PDF-" + b"x" * (10 * 1024 * 1024 + 1)

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_p4/media",
            files={"file": ("big.pdf", big, "application/pdf")},
        )
    assert res.status_code == 413


def test_upload_pdf_between_5_and_10_mb_is_accepted(client_and_vault):
    """El cap de 5 MB es de IMÁGENES: un PDF de 6 MB pasa (Meta acepta
    documentos hasta 100 MB; nosotros capeamos en 10)."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p5", {"active_route": "humano"})
    six_mb = b"%PDF-" + b"x" * (6 * 1024 * 1024)

    with patch(
        "src.plugins.chats.api.handoff.upload_media",
        new=AsyncMock(return_value="m-6mb"),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p5/media",
            files={"file": ("seis.pdf", six_mb, "application/pdf")},
        )
    assert res.status_code == 200, res.text


def test_upload_image_cap_still_5mb(client_and_vault):
    """Regresión: subir el cap de PDF no afloja el de imagen."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_p6", {"active_route": "humano"})
    big_img = b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024)

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_p6/media",
            files={"file": ("big.jpg", big_img, "image/jpeg")},
        )
    assert res.status_code == 413


# ---------- POST /messages con attachment documento (fase B: send) ----------


def _seed_document_attachment(vault, session_id: str) -> None:
    _write_metadata(
        vault,
        session_id,
        {
            "active_route": "humano",
            "service_window_expires_at_ms": _FUTURE_MS,
            "outbound_media": {
                "meta-media-pdf-1": {
                    "media_ref": f"/api/dashboard/media/{session_id}/out-xyz.pdf",
                    "filename": "out-xyz.pdf",
                    "kind": "document",
                    "filename_display": "comprobante.pdf",
                }
            },
        },
    )


def test_send_document_attachment_uses_send_document_and_persists_fields(
    client_and_vault,
):
    client, vault = client_and_vault
    _seed_document_attachment(vault, "wa_p7")

    ok = OutboundResult(wa_message_id="wamid.p7", ok=True)
    send_doc_mock = AsyncMock(return_value=ok)
    send_img_mock = AsyncMock()
    with (
        patch(
            "src.plugins.chats.api.handoff.send_document_to_session",
            new=send_doc_mock,
        ),
        patch("src.plugins.chats.api.handoff.send_image_to_session", new=send_img_mock),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p7/messages",
            json={
                "attachment_id": "meta-media-pdf-1",
                "text": "Ahí va el comprobante 🤍",
                "client_message_id": "cmid-p7",
            },
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["sender"] == "human"
    assert body["document_url"] == "/api/dashboard/media/wa_p7/out-xyz.pdf"
    assert body["document_filename"] == "comprobante.pdf"
    # Se envió como DOCUMENTO (no imagen), con caption y filename.
    send_img_mock.assert_not_awaited()
    send_doc_mock.assert_awaited_once()
    call = send_doc_mock.await_args
    merged = dict(zip(("session_id", "media_id", "caption", "filename"), call.args))
    merged.update(call.kwargs)
    assert merged["media_id"] == "meta-media-pdf-1"
    assert merged["caption"] == "Ahí va el comprobante 🤍"
    assert merged["filename"] == "comprobante.pdf"
    # Evento humano persistido con los campos de documento.
    log = vault / "wa_p7" / "sessions" / "wa_p7.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["sender"] == "human"
    assert parsed["document_url"] == "/api/dashboard/media/wa_p7/out-xyz.pdf"
    assert parsed["document_filename"] == "comprobante.pdf"
    assert "image_url" not in parsed


def test_send_document_rejection_returns_502(client_and_vault):
    client, vault = client_and_vault
    _seed_document_attachment(vault, "wa_p8")

    fail = OutboundResult(wa_message_id=None, ok=False, error="http_400: bad media")
    with patch(
        "src.plugins.chats.api.handoff.send_document_to_session",
        new=AsyncMock(return_value=fail),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p8/messages",
            json={"attachment_id": "meta-media-pdf-1", "client_message_id": "c8"},
        )
    assert res.status_code == 502
    data = _read_metadata(vault, "wa_p8")
    assert "c8" not in data.get("pending_human_sends", {})


def test_send_document_timeout_returns_504_and_keeps_reservation(client_and_vault):
    """PM2-B1b aplica igual a documentos: timeout ≠ rechazo."""
    client, vault = client_and_vault
    _seed_document_attachment(vault, "wa_p9")

    timeout = OutboundResult(wa_message_id=None, ok=False, error="timeout: read")
    with patch(
        "src.plugins.chats.api.handoff.send_document_to_session",
        new=AsyncMock(return_value=timeout),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p9/messages",
            json={"attachment_id": "meta-media-pdf-1", "client_message_id": "c9"},
        )
    assert res.status_code == 504
    data = _read_metadata(vault, "wa_p9")
    assert "c9" in data.get("pending_human_sends", {})


def test_replay_of_document_send_returns_document_fields(client_and_vault):
    """PM-B5 para documentos: el replay idempotente devuelve los campos de
    documento sin re-enviar."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_p10",
        {
            "active_route": "humano",
            "service_window_expires_at_ms": _FUTURE_MS,
            "sent_human_message_ids": ["cmid-ya"],
            "outbound_media": {
                "m-doc": {
                    "media_ref": "/api/dashboard/media/wa_p10/out-9.pdf",
                    "kind": "document",
                    "filename_display": "recibo.pdf",
                }
            },
        },
    )

    send_doc_mock = AsyncMock()
    with patch(
        "src.plugins.chats.api.handoff.send_document_to_session", new=send_doc_mock
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_p10/messages",
            json={"attachment_id": "m-doc", "client_message_id": "cmid-ya"},
        )
    assert res.status_code == 200, res.text
    assert res.json()["document_url"] == "/api/dashboard/media/wa_p10/out-9.pdf"
    assert res.json()["document_filename"] == "recibo.pdf"
    send_doc_mock.assert_not_awaited()


def test_get_session_history_projects_document_fields(client_and_vault):
    """Cierre del círculo: tras el send de un documento, el GET del dashboard
    devuelve el evento humano CON `document_url`/`document_filename` (el
    frontend pinta el chip desde acá — no desde la respuesta del POST)."""
    client, vault = client_and_vault
    _seed_document_attachment(vault, "wa_p11")

    ok = OutboundResult(wa_message_id="wamid.p11", ok=True)
    with patch(
        "src.plugins.chats.api.handoff.send_document_to_session",
        new=AsyncMock(return_value=ok),
    ):
        sent = client.post(
            "/api/dashboard/sessions/wa_p11/messages",
            json={
                "attachment_id": "meta-media-pdf-1",
                "text": "comprobante adjunto",
                "client_message_id": "cmid-p11",
            },
        )
    assert sent.status_code == 200, sent.text

    res = client.get("/api/dashboard/sessions/wa_p11")
    assert res.status_code == 200, res.text
    human = [
        m for m in res.json()["messages"] if m.get("ui_type") == "human_message"
    ]
    assert len(human) == 1
    assert human[0]["document_url"] == "/api/dashboard/media/wa_p11/out-xyz.pdf"
    assert human[0]["document_filename"] == "comprobante.pdf"
