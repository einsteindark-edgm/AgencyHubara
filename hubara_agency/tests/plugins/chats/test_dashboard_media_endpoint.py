"""Tests del endpoint `GET /api/dashboard/media/{session_id}/{filename}`.

Sirve las imágenes inbound que el cliente envió por WhatsApp (comprobantes de
pago / fotos) para que el operador humano las vea en el chat. La resolución
segura (anti path-traversal) está cubierta en
`tests/platform/media/test_media_store.py`; acá validamos el wiring HTTP:
sirve los bytes con 200 y devuelve 404 cuando el archivo no existe.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_vault(tmp_path):
    with patch("src.platform.media.store.WORKSPACE_VAULT_DIR", tmp_path):
        from src.main import app

        yield TestClient(app), tmp_path


def _seed_image(vault, session_id: str, filename: str, data: bytes) -> None:
    media_dir = vault / session_id / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / filename).write_bytes(data)


def test_serves_persisted_image(client_and_vault):
    client, vault = client_and_vault
    _seed_image(vault, "wa_573001112233", "receipt_1.jpg", b"JPEGBYTES")

    resp = client.get("/api/dashboard/media/wa_573001112233/receipt_1.jpg")

    assert resp.status_code == 200
    assert resp.content == b"JPEGBYTES"
    assert resp.headers["content-type"].startswith("image/")


def test_unknown_file_returns_404(client_and_vault):
    client, _ = client_and_vault
    resp = client.get("/api/dashboard/media/wa_573001112233/nope.jpg")
    assert resp.status_code == 404
