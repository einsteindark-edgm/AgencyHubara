"""Tests de envío de FOTOS del operador humano (dashboard handoff).

Dos fases (des-acopladas a propósito, para que un retry del send NUNCA
re-suba los bytes):

  * `POST /api/dashboard/sessions/{id}/media` (multipart) → persiste la foto
    en el vault + la sube a Meta (`upload_media`) → devuelve `attachment_id`
    (el media_id de Meta) + `media_ref` (url servible por el dashboard).

  * `POST /api/dashboard/sessions/{id}/messages` extendido con
    `{attachment_id, text?, client_message_id?}` → manda la foto al cliente
    vía `send_image(media_id=...)` + persiste el evento humano con `image_url`.

Guards nuevos que también cubren estos tests:
  * ventana de servicio 24h cerrada → 409 (fix del fallo silencioso previo).
  * idempotencia por `client_message_id` → replay sin re-enviar.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


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


# Ventana de servicio abierta (evita que el guard 24h bloquee) — 1h en el futuro.
_FUTURE_MS = 9_999_999_999_000


# ---------- POST /media (fase A: upload) ----------


def test_upload_media_persists_and_returns_attachment_id(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_1", {"active_route": "humano"})

    upload_mock = AsyncMock(return_value="meta-media-777")
    with patch("src.plugins.chats.api.handoff.upload_media", new=upload_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_1/media",
            files={"file": ("foto.jpg", b"\xff\xd8\xff-bytes", "image/jpeg")},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attachment_id"] == "meta-media-777"
    assert body["media_ref"].startswith("/api/dashboard/media/wa_1/")
    # Se subió a Meta con los bytes correctos.
    upload_mock.assert_awaited_once()
    args = upload_mock.await_args.args
    assert args[1] == b"\xff\xd8\xff-bytes"
    # Persistido en disco y servible.
    filename = body["media_ref"].split("/")[-1]
    stored = vault / "wa_1" / "media" / filename
    assert stored.exists()
    assert stored.read_bytes() == b"\xff\xd8\xff-bytes"


def test_upload_media_rejects_when_not_humano(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_2", {"active_route": "ventas"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_2/media",
            files={"file": ("f.jpg", b"x", "image/jpeg")},
        )
    assert res.status_code == 409


def test_upload_media_rejects_unsupported_mime(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_3", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_3/media",
            files={"file": ("f.gif", b"GIF89a", "image/gif")},
        )
    assert res.status_code == 415


def test_upload_media_rejects_oversize(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_4", {"active_route": "humano"})
    big = b"x" * (5 * 1024 * 1024 + 1)  # > 5 MB

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_4/media",
            files={"file": ("big.jpg", big, "image/jpeg")},
        )
    assert res.status_code == 413


def test_upload_media_returns_502_when_meta_upload_fails(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_5", {"active_route": "humano"})

    from src.platform.whatsapp.client import MediaUploadError

    boom = AsyncMock(side_effect=MediaUploadError("meta down"))
    with patch("src.plugins.chats.api.handoff.upload_media", new=boom):
        res = client.post(
            "/api/dashboard/sessions/wa_5/media",
            files={"file": ("f.jpg", b"\xff\xd8\xff-jpeg", "image/jpeg")},
        )
    assert res.status_code == 502


# ---------- POST /messages con attachment (fase B: send) ----------


def test_send_message_with_attachment_sends_image_and_persists_image_url(client_and_vault):
    client, vault = client_and_vault
    # Registrar el attachment como si la fase A ya hubiera corrido.
    _write_metadata(
        vault,
        "wa_6",
        {
            "active_route": "humano",
            "service_window_expires_at_ms": _FUTURE_MS,
            "outbound_media": {
                "meta-media-1": {
                    "media_ref": "/api/dashboard/media/wa_6/out-xyz.jpg",
                    "filename": "out-xyz.jpg",
                }
            },
        },
    )

    send_image_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_image_to_session", new=send_image_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_6/messages",
            json={
                "attachment_id": "meta-media-1",
                "text": "Mirá el color 🤍",
                "client_message_id": "cmid-1",
            },
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["sender"] == "human"
    # send_image_to_session llamado con el media_id + caption.
    send_image_mock.assert_awaited_once()
    kwargs = send_image_mock.await_args.kwargs
    call = {**dict(zip(("session_id", "media_id", "caption"), send_image_mock.await_args.args)), **kwargs}
    assert call.get("media_id", None) == "meta-media-1" or "meta-media-1" in send_image_mock.await_args.args
    # Evento humano persistido con image_url.
    log = vault / "wa_6" / "sessions" / "wa_6.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["sender"] == "human"
    assert parsed["image_url"] == "/api/dashboard/media/wa_6/out-xyz.jpg"


def test_send_message_blocks_when_service_window_closed(client_and_vault):
    """Fix del bug latente: hoy el operador manda free-form sin chequear la
    ventana 24h → Meta lo rechaza en silencio. Ahora: 409 accionable."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_7",
        {"active_route": "humano", "service_window_expires_at_ms": 1},  # cerrada
    )

    with patch("src.plugins.chats.api.handoff.send_message_to_session", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_7/messages",
            json={"text": "hola tardío"},
        )
    assert res.status_code == 409
    assert "ventana" in res.json()["detail"].lower()


def test_send_message_allowed_when_window_field_absent(client_and_vault):
    """Fail-open: sin `service_window_expires_at_ms` (dev/seed) NO se bloquea —
    preserva el comportamiento previo del endpoint."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_8", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.send_message_to_session", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_8/messages",
            json={"text": "hola"},
        )
    assert res.status_code == 200


def test_send_message_is_idempotent_by_client_message_id(client_and_vault):
    """Un retry del send con el mismo client_message_id NO re-envía a WhatsApp."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_9",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )

    send_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_message_to_session", new=send_mock):
        r1 = client.post(
            "/api/dashboard/sessions/wa_9/messages",
            json={"text": "hola", "client_message_id": "dup-1"},
        )
        r2 = client.post(
            "/api/dashboard/sessions/wa_9/messages",
            json={"text": "hola", "client_message_id": "dup-1"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Solo el primer POST realmente envió a WhatsApp.
    assert send_mock.await_count == 1


def test_send_message_requires_text_or_attachment(client_and_vault):
    """Body sin text y sin attachment_id → 422/400 (nada que mandar)."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_10",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )

    res = client.post("/api/dashboard/sessions/wa_10/messages", json={})
    assert res.status_code in (400, 422)


# ---------- Fixes del premortem (auditoría 2026-07-08) ----------


def test_send_with_unknown_attachment_id_rejected_422(client_and_vault):
    """PM-B3: un attachment_id que NUNCA se subió para esta sesión NO se
    reenvía a Meta (podría ser el media_id de otra sesión → filtración)."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_pm3",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )

    send_image_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_image_to_session", new=send_image_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_pm3/messages",
            json={"attachment_id": "media-ajena", "client_message_id": "c1"},
        )
    assert res.status_code == 422
    send_image_mock.assert_not_awaited()


def test_attachment_requires_client_message_id(client_and_vault):
    """PM-B4: sin client_message_id no hay NINGÚN dedup para fotos (el texto
    tiene fingerprint; la imagen no) → lo exigimos en el contrato."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_pm4",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )
    res = client.post(
        "/api/dashboard/sessions/wa_pm4/messages",
        json={"attachment_id": "media-1"},
    )
    assert res.status_code == 422


def test_replay_wins_over_closed_window_and_returns_image_url(client_and_vault):
    """PM-B5: un retry de algo YA enviado debe responder 200 replay (con su
    image_url) aunque la ventana 24h haya cerrado entre medio — el guard de
    ventana aplica a envíos NUEVOS, no a replays."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_pm5",
        {
            "active_route": "humano",
            "service_window_expires_at_ms": 1,  # cerrada
            "sent_human_message_ids": ["cmid-ya-enviado"],
            "outbound_media": {
                "media-9": {"media_ref": "/api/dashboard/media/wa_pm5/out-9.jpg"}
            },
        },
    )

    send_image_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_image_to_session", new=send_image_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_pm5/messages",
            json={
                "attachment_id": "media-9",
                "client_message_id": "cmid-ya-enviado",
                "text": "hola",
            },
        )
    assert res.status_code == 200, res.text
    assert res.json()["image_url"] == "/api/dashboard/media/wa_pm5/out-9.jpg"
    send_image_mock.assert_not_awaited()


def test_upload_media_rejects_spoofed_mime(client_and_vault):
    """PM-B7: content-type image/jpeg con bytes que NO son JPEG/PNG → 415."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_pm7", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_pm7/media",
            files={"file": ("evil.jpg", b"#!/bin/sh\necho pwned", "image/jpeg")},
        )
    assert res.status_code == 415


def test_upload_media_rejects_empty_file(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_pm11", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_pm11/media",
            files={"file": ("nada.jpg", b"", "image/jpeg")},
        )
    assert res.status_code == 400


def test_unsafe_session_id_rejected_on_media_and_messages(client_and_vault):
    """PM-B9: session_id con `..` no llega a los stores (simetría con el GET)."""
    client, vault = client_and_vault
    _write_metadata(vault, "a..b", {"active_route": "humano"})

    with patch("src.plugins.chats.api.handoff.upload_media", new=AsyncMock()):
        r1 = client.post(
            "/api/dashboard/sessions/a..b/media",
            files={"file": ("f.jpg", b"\xff\xd8\xffx", "image/jpeg")},
        )
    r2 = client.post(
        "/api/dashboard/sessions/a..b/messages",
        json={"text": "hola"},
    )
    assert r1.status_code == 400
    assert r2.status_code == 400


def test_outbound_media_is_pruned_to_cap(client_and_vault):
    """PM-B6: outbound_media no crece sin límite — se poda a las últimas 100."""
    client, vault = client_and_vault
    seeded = {
        f"old-{i}": {"media_ref": f"/api/dashboard/media/wa_pm6/out-{i}.jpg"}
        for i in range(100)
    }
    _write_metadata(
        vault, "wa_pm6", {"active_route": "humano", "outbound_media": seeded}
    )

    with patch(
        "src.plugins.chats.api.handoff.upload_media",
        new=AsyncMock(return_value="media-nueva"),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_pm6/media",
            files={"file": ("f.jpg", b"\xff\xd8\xff-jpg", "image/jpeg")},
        )
    assert res.status_code == 200
    data = _read_metadata(vault, "wa_pm6")
    assert len(data["outbound_media"]) == 100
    assert "media-nueva" in data["outbound_media"]
    assert "old-0" not in data["outbound_media"]  # el más viejo salió


def test_send_does_not_clobber_concurrent_metadata_writes(client_and_vault):
    """PM-B1 (lost-update): si OTRO writer toca metadata mientras el send está
    en vuelo (p.ej. el ingest renueva la ventana), esa escritura debe
    SOBREVIVIR al write final del endpoint (lectura fresca, no dict stale)."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_pm1",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )

    async def _concurrent_writer(session_id, text):
        # Simula al ingest escribiendo DURANTE el send.
        data = _read_metadata(vault, session_id)
        data["concurrent_marker"] = "escrito-durante-el-send"
        _write_metadata(vault, session_id, data)

    with patch(
        "src.plugins.chats.api.handoff.send_message_to_session",
        new=AsyncMock(side_effect=_concurrent_writer),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_pm1/messages",
            json={"text": "hola", "client_message_id": "cm-pm1"},
        )

    assert res.status_code == 200
    data = _read_metadata(vault, "wa_pm1")
    # La escritura concurrente sobrevivió Y el cmid quedó registrado.
    assert data.get("concurrent_marker") == "escrito-durante-el-send"
    assert "cm-pm1" in data.get("sent_human_message_ids", [])


def test_send_message_text_only_still_works(client_and_vault):
    """Regresión: el path de solo-texto (sin attachment) sigue funcionando."""
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_11",
        {"active_route": "humano", "service_window_expires_at_ms": _FUTURE_MS},
    )

    send_mock = AsyncMock()
    with patch("src.plugins.chats.api.handoff.send_message_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_11/messages",
            json={"text": "hola equipo"},
        )
    assert res.status_code == 200
    send_mock.assert_awaited_once_with("wa_11", "hola equipo")
