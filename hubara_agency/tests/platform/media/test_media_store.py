"""Tests del media store filesystem (imágenes inbound de WhatsApp).

Cubre el contrato que consume el ingest (persistir) y el endpoint del dashboard
(resolver para servir), con énfasis en las guardas anti path-traversal — el
`filename`/`session_id` del endpoint vienen de la URL (input no confiable).
"""
from __future__ import annotations

import pytest

import src.platform.media.store as media_store


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    return tmp_path


def test_persist_then_resolve_roundtrip(vault):
    filename = media_store.persist_inbound_image(
        "wa_57300", "123456789", b"\x89PNG-bytes", "image/png"
    )
    assert filename == "123456789.png"

    path = media_store.resolve_media_file("wa_57300", filename)
    assert path is not None
    assert path.read_bytes() == b"\x89PNG-bytes"
    assert path == vault / "wa_57300" / "media" / "123456789.png"


def test_media_url_for_points_at_dashboard_endpoint():
    assert (
        media_store.media_url_for("wa_57300", "abc.jpg")
        == "/api/dashboard/media/wa_57300/abc.jpg"
    )


def test_mime_to_extension(vault):
    assert media_store.persist_inbound_image("wa_1", "a", b"x", "image/jpeg").endswith(
        ".jpg"
    )
    assert media_store.persist_inbound_image("wa_1", "b", b"x", "image/webp").endswith(
        ".webp"
    )
    # mime desconocido / None → default jpg (no rompemos por un mime raro).
    assert media_store.persist_inbound_image("wa_1", "c", b"x", None).endswith(".jpg")
    assert media_store.persist_inbound_image(
        "wa_1", "d", b"x", "application/octet-stream"
    ).endswith(".jpg")


def test_media_id_with_unsafe_chars_is_sanitized(vault):
    # Meta a veces usa media_ids con ':' o '/'.
    filename = media_store.persist_inbound_image(
        "wa_1", "4944:12/34", b"x", "image/jpeg"
    )
    assert "/" not in filename
    assert ":" not in filename
    # El archivo se puede resolver con el filename sanitizado.
    assert media_store.resolve_media_file("wa_1", filename) is not None


def test_persist_is_idempotent(vault):
    f1 = media_store.persist_inbound_image("wa_1", "same", b"first", "image/jpeg")
    # Segundo intento con bytes distintos NO reescribe (mismo media_id).
    f2 = media_store.persist_inbound_image("wa_1", "same", b"second", "image/jpeg")
    assert f1 == f2
    path = media_store.resolve_media_file("wa_1", f1)
    assert path is not None
    assert path.read_bytes() == b"first"


def test_resolve_rejects_path_traversal(vault):
    media_store.persist_inbound_image("wa_1", "ok", b"x", "image/jpeg")
    # Crear un secreto fuera del dir de media de la sesión.
    secret = vault / "secret.txt"
    secret.write_text("top secret")

    # Intentos de traversal en el filename → None (no se sirve nada).
    assert media_store.resolve_media_file("wa_1", "../../secret.txt") is None
    assert media_store.resolve_media_file("wa_1", "../secret.txt") is None
    assert media_store.resolve_media_file("wa_1", "..") is None
    assert media_store.resolve_media_file("wa_1", "a/b") is None
    # Traversal en el session_id también se rechaza.
    assert media_store.resolve_media_file("../wa_1", "ok.jpg") is None
    assert media_store.resolve_media_file("wa_1/..", "ok.jpg") is None


def test_resolve_missing_file_returns_none(vault):
    assert media_store.resolve_media_file("wa_1", "nope.jpg") is None


def test_session_id_with_e164_plus_is_served(vault):
    # session_id de WhatsApp con prefijo E.164 (`wa_+573...`) — el `+` debe ser
    # un segmento válido, si no las sesiones reales no mostrarían sus imágenes.
    media_store.persist_inbound_image(
        "wa_+573001112233", "media99", b"x", "image/jpeg"
    )
    path = media_store.resolve_media_file("wa_+573001112233", "media99.jpg")
    assert path is not None
    # Pero el `+` no relaja la defensa de traversal.
    assert media_store.resolve_media_file("wa_+573001112233", "../x") is None
